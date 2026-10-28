"""Shared wiring for every surface: building agents, unattended runs, session listing."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .agent import Agent
from .config import Mode, Settings
from .events import deny_all
from .scheduler import Scheduler, ScheduleStore


def unattended_run(settings: Settings, task: dict) -> str:
    """Run a scheduled directive with nobody watching. Approval-gated actions are refused; never
    more permissive than `edits` mode, even if the operator's default is autonomous."""
    mode = settings.mode if settings.mode in (Mode.observe, Mode.ask, Mode.edits) else Mode.edits
    s = settings.model_copy(update={"mode": mode})
    agent = Agent(s, approver=deny_all, surface="scheduler", unattended=True)
    agent.title = f"[scheduled] {task['name']}"
    report = agent.run(task["directive"])
    out = settings.bunker / "reports" / f"{time.strftime('%Y%m%d-%H%M')}-{task['id']}.md"
    out.write_text(
        f"# {task['name']}\n\n- directive: {task['directive']}\n- session: {agent.session_id}\n"
        f"- cost: ${agent.budget.meter.usd:.4f}\n\n{report}\n",
        encoding="utf-8",
    )
    return report


def start_scheduler(settings: Settings) -> Scheduler:
    sched = Scheduler(ScheduleStore(settings.bunker / "schedule.json"), lambda t: unattended_run(settings, t))
    sched.start()
    return sched


def list_sessions(settings: Settings, limit: int = 30) -> list[dict]:
    rows = []
    folder = settings.bunker / "sessions"
    for p in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        rows.append({"id": d["id"], "title": d.get("title", ""), "surface": d.get("surface", ""),
                     "updated": d.get("updated", 0), "usd": d.get("usd", 0), "turns": len(d.get("messages", []))})
    return rows


def transcript(messages: list[dict]) -> list[dict]:
    """Flatten stored API messages into display rows for UIs."""
    rows = []
    for m in messages:
        content = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
        for b in content:
            t = b.get("type")
            if t == "text" and b.get("text"):
                rows.append({"who": m["role"], "kind": "text", "text": b["text"]})
            elif t == "tool_use":
                rows.append({"who": "unit", "kind": "tool_call", "tool": b["name"], "text": json.dumps(b.get("input"))[:300]})
            elif t == "tool_result":
                c = b.get("content")
                text = c if isinstance(c, str) else json.dumps(c)
                rows.append({"who": "harness", "kind": "tool_result", "ok": not b.get("is_error"), "text": text[:2000]})
    return rows


def init_project(root: Path) -> list[str]:
    from .config import DEFAULT_TOML

    made = []
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "pale.toml").exists():
        (root / "pale.toml").write_text(DEFAULT_TOML, encoding="utf-8")
        made.append("pale.toml")
    for d in ("sandbox", "bunker/memory", "bunker/skills/_quarantine", "bunker/sessions", "bunker/checkpoints", "bunker/reports"):
        if not (root / d).exists():
            (root / d).mkdir(parents=True)
            made.append(d + "/")
    pm = root / "sandbox" / "PALE.md"
    if not pm.exists():
        pm.write_text(
            "# Project instructions\n\nThe unit reads this file at the start of every directive.\n"
            "Put conventions here: how to run tests, code style, things to never touch.\n",
            encoding="utf-8",
        )
        made.append("sandbox/PALE.md")
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(".env\n.venv/\n__pycache__/\nbunker/\n", encoding="utf-8")
        made.append(".gitignore")
    return made
