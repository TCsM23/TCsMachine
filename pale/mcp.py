"""Minimal MCP client over stdio (JSON-RPC 2.0, one message per line).

Each server configured under [mcp.<name>] in pale.toml is started once per workspace and its
tools are exposed to the unit as `mcp__<name>__<tool>`. Untrusted servers' tools count as
mutating, so they go through the same approval gate, risk display and black box as built-ins.
Servers get the scrubbed environment (no API keys) plus whatever `env` you configure for them.
"""

from __future__ import annotations

import atexit
import itertools
import json
import re
import subprocess
import threading
from concurrent.futures import Future
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from . import __version__
from .config import MCPServer, Settings
from .permissions import Risk
from .redact import scrubbed_env
from .tools.base import Tool, ToolError

PROTOCOL = "2025-06-18"


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, name: str, cfg: MCPServer, cwd: Path):
        self.name = name
        self.cfg = cfg
        env = {**scrubbed_env(), **cfg.env}
        self.proc = subprocess.Popen(
            [cfg.command, *cfg.args], cwd=cwd, env=env, text=True, bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True, name=f"mcp-{name}").start()
        info = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                           "clientInfo": {"name": "pale", "version": __version__}})
        self.server_info = info.get("serverInfo", {})
        self.notify("notifications/initialized")

    def _reader(self) -> None:
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # servers sometimes log to stdout; ignore non-JSON
            fut = self._pending.pop(msg.get("id"), None) if "id" in msg else None
            if fut is None:
                continue  # a notification or a server->client request we don't support
            if "error" in msg:
                fut.set_exception(MCPError(msg["error"].get("message", str(msg["error"]))))
            else:
                fut.set_result(msg.get("result", {}))
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(MCPError(f"MCP server {self.name} exited"))

    def _send(self, msg: dict) -> None:
        with self._lock:
            if self.proc.poll() is not None:
                raise MCPError(f"MCP server {self.name} is not running")
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        rid = next(self._ids)
        fut: Future = Future()
        self._pending[rid] = fut
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            return fut.result(timeout)
        except TimeoutError:
            self._pending.pop(rid, None)
            raise MCPError(f"{self.name}: {method} timed out after {timeout}s")

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    def call(self, tool: str, args: dict, timeout: float = 120) -> tuple[str, bool]:
        res = self.request("tools/call", {"name": tool, "arguments": args}, timeout)
        parts = []
        for c in res.get("content", []):
            if c.get("type") == "text":
                parts.append(c["text"])
            elif c.get("type") == "resource":
                parts.append(json.dumps(c.get("resource", {}))[:4000])
            else:
                parts.append(f"[{c.get('type')} content omitted]")
        if res.get("structuredContent") and not parts:
            parts.append(json.dumps(res["structuredContent"]))
        return "\n".join(parts) or "(no output)", bool(res.get("isError"))

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class AnyInput(BaseModel):
    model_config = ConfigDict(extra="allow")


def tool_name(server: str, tool: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp__{server}__{tool}")[:64]


class MCPTool(Tool):
    Input = AnyInput

    def __init__(self, client: MCPClient, spec: dict):
        self.client = client
        self.remote = spec["name"]
        self.name = tool_name(client.name, spec["name"])  # type: ignore[misc]
        self.description = f"[MCP {client.name}] " + (spec.get("description") or spec["name"])[:1500]  # type: ignore[misc]
        self.input_schema = spec.get("inputSchema") or {"type": "object", "properties": {}}
        self.mutates = not client.cfg.trusted  # type: ignore[misc]
        self.parallel_safe = client.cfg.trusted  # type: ignore[misc]

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def arg(self, inp) -> str:
        return json.dumps(inp.model_dump(), ensure_ascii=False)[:300]

    def risk(self, inp, ctx) -> Risk:
        if self.client.cfg.trusted:
            return Risk("low", [f"trusted MCP server {self.client.name}"])
        return Risk("medium", [f"third-party MCP tool from {self.client.name}"])

    def preview(self, inp, ctx) -> str:
        return json.dumps(inp.model_dump(), indent=2, ensure_ascii=False)

    def run(self, inp, ctx) -> str:
        try:
            out, is_error = self.client.call(self.remote, inp.model_dump())
        except MCPError as e:
            raise ToolError(str(e))
        if is_error:
            raise ToolError(out)
        return out


# one set of servers per workspace, shared by every agent in this process
_registry: dict[str, tuple[list[MCPClient], list[MCPTool], list[str]]] = {}
_reg_lock = threading.Lock()


def tools_for(settings: Settings) -> tuple[list[MCPTool], list[str]]:
    """Start (once) the workspace's MCP servers. Returns (tools, errors)."""
    key = str(settings.root.resolve())
    with _reg_lock:
        if key in _registry:
            return _registry[key][1], _registry[key][2]
        clients, tools, errors = [], [], []
        for name, cfg in settings.mcp.items():
            if not cfg.enabled:
                continue
            try:
                c = MCPClient(name, cfg, settings.sandbox)
                clients.append(c)
                tools += [MCPTool(c, spec) for spec in c.list_tools()]
            except (OSError, MCPError) as e:
                errors.append(f"MCP server {name} failed to start: {e}")
        _registry[key] = (clients, tools, errors)
        return tools, errors


def shutdown(settings: Settings | None = None) -> None:
    with _reg_lock:
        keys = [str(settings.root.resolve())] if settings else list(_registry)
        for k in keys:
            for c in _registry.pop(k, ([], [], []))[0]:
                c.close()


atexit.register(shutdown)
