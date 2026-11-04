"""Local web terminal (also the desktop app's UI): FastAPI + one WebSocket per open window.

Security: binds to 127.0.0.1, and every request needs the random token handed to the window
at startup (a page on another site can't guess it, so it can't drive your agent). The agent runs
in a worker thread; approvals block that thread until the window answers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse

from .. import __version__, blackbox, config
from ..agent import Agent
from ..config import EDITABLE, Mode, Settings
from ..events import ApprovalDecision, ApprovalRequest, Event
from ..jobs import jobs_for
from ..memory import Memory, Skills, render, slugify
from ..permissions import SandboxViolation
from ..runtime import fork_session, init_project, list_sessions, search_sessions, transcript, unattended_run
from ..scheduler import Scheduler, ScheduleStore
from ..units import load_units

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 20_000_000
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv"}


class Bridge:
    """Connects one Agent (worker thread) to one WebSocket (event loop)."""

    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop):
        self.ws = ws
        self.loop = loop
        self.out: asyncio.Queue = asyncio.Queue()
        self.pending: dict[str, tuple[threading.Event, dict]] = {}

    def send(self, msg: dict) -> None:
        self.loop.call_soon_threadsafe(self.out.put_nowait, msg)

    def sink(self, ev: Event) -> None:
        self.send({"type": "event", "kind": ev.kind, "data": ev.data})

    def _wait(self, key: str, msg: dict, timeout: float = 900) -> dict:
        done, box = threading.Event(), {}
        self.pending[key] = (done, box)
        self.send(msg)
        if not done.wait(timeout):
            self.pending.pop(key, None)
            return {}
        return box

    def approver(self, req: ApprovalRequest) -> ApprovalDecision:
        key = req.call_id or secrets.token_hex(4)
        ans = self._wait(key, {"type": "approval", "id": key, "tool": req.tool, "summary": req.summary,
                               "risk": req.risk, "reasons": req.reasons, "preview": req.preview})
        if not ans:
            return ApprovalDecision(False, "no answer from the operator within 15 minutes")
        always = bool(ans.get("always")) and req.risk != "high"
        return ApprovalDecision(bool(ans.get("approved")), ans.get("note", ""), always)

    def ask(self, question: str) -> str:
        return self._wait("question", {"type": "question", "text": question}).get("text", "")

    def answer(self, key: str, payload: dict) -> None:
        if key in self.pending:
            done, box = self.pending.pop(key)
            box.update(payload)
            done.set()


class Context:
    """Mutable server state: the active workspace can change while the app runs."""

    def __init__(self, settings: Settings, model=None, desktop=None, scheduler: bool = False):
        settings.ensure_dirs()
        self.settings = settings
        self.model = model
        self.desktop = desktop
        self.bridges: set[Bridge] = set()
        self.want_scheduler = scheduler
        self.scheduler: Scheduler | None = None
        self._start_scheduler()
        self._watch_jobs()

    def _watch_jobs(self) -> None:
        jobs = jobs_for(self.settings)
        if not getattr(jobs, "_pale_web", False):
            jobs.listeners.append(lambda info: self.broadcast({"type": "job", "job": info}))
            jobs._pale_web = True

    @property
    def b(self) -> Path:
        return self.settings.bunker

    def broadcast(self, msg: dict) -> None:
        for br in list(self.bridges):
            br.send(msg)

    def _start_scheduler(self) -> None:
        if not self.want_scheduler:
            return
        if self.scheduler:
            self.scheduler.stop()
        s = self.settings
        self.scheduler = Scheduler(ScheduleStore(s.bunker / "schedule.json"), lambda t: unattended_run(s, t),
                                   watch_root=s.sandbox)
        self.scheduler.notifiers.append(
            lambda task, report: self.broadcast({"type": "scheduled", "name": task["name"], "report": report[:4000]}))
        self.scheduler.start()

    def switch(self, root: Path) -> Settings:
        init_project(root)
        self.settings = config.load(root)
        self.settings.ensure_dirs()
        self._start_scheduler()
        self._watch_jobs()
        self.broadcast({"type": "workspace", "path": str(root)})
        return self.settings


def create_app(settings: Settings, model=None, desktop=None, scheduler: bool = False) -> FastAPI:
    """`model` overrides the ModelClient (tests pass a FakeModel). `desktop` adds the desktop-only
    endpoints (setup wizard, workspaces, updater, native dialogs)."""
    ctx = Context(settings, model, desktop, scheduler)
    app = FastAPI(title="PALE", docs_url=None, redoc_url=None)
    app.state.token = secrets.token_urlsafe(18)
    app.state.ctx = ctx

    def auth(x_pale_token: str = Header("")):
        if not secrets.compare_digest(x_pale_token, app.state.token):
            raise HTTPException(401, "bad token")

    A = [Depends(auth)]

    # ------------------------------------------------------------------ static
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/static/{name}")
    def static(name: str):
        p = (STATIC / name).resolve()
        if p.parent != STATIC.resolve() or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ state & settings
    @app.get("/api/state", dependencies=A)
    def state():
        s = ctx.settings
        return {"unit": s.unit_name, "model": s.model, "mode": s.mode.value, "dry_run": s.dry_run,
                "sandbox": str(s.sandbox), "root": str(s.root), "version": __version__,
                "desktop": ctx.desktop is not None, "platform": sys.platform,
                "budget": {"session": s.budget_session_usd, "daily": s.budget_daily_usd},
                "mcp": list(s.mcp), "web_search": s.web_search, "review": s.review,
                "units": sorted(load_units(s.units_dir)),
                "has_key": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))}

    @app.get("/api/settings", dependencies=A)
    def get_settings():
        d = ctx.settings.model_dump(mode="json")
        return {k: d[k] for k in EDITABLE}

    @app.put("/api/settings", dependencies=A)
    def put_settings(body: dict):
        patch = {k: v for k, v in body.items() if k in EDITABLE}
        try:
            new = Settings.model_validate({**ctx.settings.model_dump(), **patch})
        except Exception as e:
            raise HTTPException(400, str(e))
        new.root = ctx.settings.root
        config.save(new)
        ctx.settings = new
        return get_settings()

    # ------------------------------------------------------------------ sessions
    def _session_file(sid: str) -> Path:
        if not re.fullmatch(r"[\w-]+", sid):
            raise HTTPException(400)
        p = ctx.b / "sessions" / f"{sid}.json"
        if not p.exists():
            raise HTTPException(404)
        return p

    @app.get("/api/sessions", dependencies=A)
    def sessions():
        return list_sessions(ctx.settings, 200)

    @app.get("/api/sessions/search", dependencies=A)
    def sessions_search(q: str = ""):
        return search_sessions(ctx.settings, q[:200])

    @app.post("/api/sessions/{sid}/fork", dependencies=A)
    def fork(sid: str, body: dict):
        _session_file(sid)
        try:
            return fork_session(ctx.settings, sid, int(body.get("turn", -1)))
        except (IndexError, ValueError) as e:
            raise HTTPException(400, str(e))

    @app.get("/api/sessions/{sid}", dependencies=A)
    def session(sid: str):
        d = json.loads(_session_file(sid).read_text())
        return {"id": d["id"], "title": d.get("title"), "todos": d.get("todos", []), "rows": transcript(d["messages"])}

    @app.delete("/api/sessions/{sid}", dependencies=A)
    def delete_session(sid: str):
        _session_file(sid).unlink()
        return {"ok": True}

    @app.patch("/api/sessions/{sid}", dependencies=A)
    def rename_session(sid: str, body: dict):
        p = _session_file(sid)
        d = json.loads(p.read_text())
        d["title"] = str(body.get("title", ""))[:120]
        p.write_text(json.dumps(d, ensure_ascii=False))
        return {"ok": True}

    @app.get("/api/sessions/{sid}/export", dependencies=A)
    def export_session(sid: str):
        d = json.loads(_session_file(sid).read_text())
        out = [f"# {d.get('title') or d['id']}", "", f"_PALE session {d['id']} · ${d.get('usd', 0):.4f}_", ""]
        for r in transcript(d["messages"]):
            if r["kind"] == "text":
                out += [f"**{'Operator' if r['who'] == 'user' else 'Unit'}:**", "", r["text"], ""]
            elif r["kind"] == "tool_call":
                out += [f"> ■ `{r['tool']}` {r['text']}", ""]
            else:
                out += ["```", r["text"][:1500], "```", ""]
        return PlainTextResponse("\n".join(out), media_type="text/markdown")

    # ------------------------------------------------------------------ memory & skills
    @app.get("/api/memory", dependencies=A)
    def memory():
        return [{"name": n.name, "description": n.description, "body": n.body} for n in Memory(ctx.b / "memory").all()]

    @app.put("/api/memory/{name}", dependencies=A)
    def put_memory(name: str, body: dict):
        n = Memory(ctx.b / "memory").put(name, body.get("description", ""), body.get("body", ""), source="operator")
        return {"name": n.name}

    @app.delete("/api/memory/{name}", dependencies=A)
    def forget(name: str):
        return {"ok": Memory(ctx.b / "memory").delete(name)}

    @app.get("/api/skills", dependencies=A)
    def skills():
        sk = Skills(ctx.b / "skills")
        row = lambda n, q: {"name": n.name, "description": n.description, "body": n.body, "quarantined": q}  # noqa: E731
        return [row(n, False) for n in sk.all()] + [row(n, True) for n in sk.quarantine.all()]

    @app.post("/api/skills/{name}/{action}", dependencies=A)
    def skill_action(name: str, action: str):
        sk = Skills(ctx.b / "skills")
        if action == "approve":
            try:
                sk.approve(name)
            except KeyError:
                raise HTTPException(404)
            return {"ok": True}
        if action == "reject":
            return {"ok": sk.reject(name)}
        if action == "delete":
            return {"ok": sk.delete(name)}
        raise HTTPException(400)

    # ------------------------------------------------------------------ specialist units & jobs
    @app.get("/api/units", dependencies=A)
    def units():
        return [{"name": u.name, "description": u.description, "model": u.model, "tools": u.tools,
                 "max_steps": u.max_steps, "body": u.body} for u in load_units(ctx.settings.units_dir).values()]

    @app.put("/api/units/{name}", dependencies=A)
    def put_unit(name: str, body: dict):
        try:
            slug = slugify(name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        extra = {k: str(body[k]) for k in ("model", "tools", "max_steps") if str(body.get(k, "")).strip()}
        if "model" in extra and not re.fullmatch(r"claude-[\w.-]+", extra["model"]):
            raise HTTPException(400, "model must be a claude-* id")
        d = ctx.settings.units_dir
        d.mkdir(exist_ok=True)
        (d / f"{slug}.md").write_text(render(slug, str(body.get("description", "")), str(body.get("body", "")), **extra),
                                      encoding="utf-8")
        return {"ok": True, "name": slug}

    @app.delete("/api/units/{name}", dependencies=A)
    def delete_unit(name: str):
        u = load_units(ctx.settings.units_dir).get(name)
        if not u or not u.path:
            raise HTTPException(404)
        u.path.unlink()
        return {"ok": True}

    @app.get("/api/jobs", dependencies=A)
    def jobs():
        return [j.info() for j in jobs_for(ctx.settings).jobs.values()]

    @app.get("/api/jobs/{jid}", dependencies=A)
    def job(jid: str):
        jb = jobs_for(ctx.settings)
        if not jb.get(jid):
            raise HTTPException(404)
        from ..redact import redact

        return {**jb.get(jid).info(), "output": redact(jb.tail(jid, 20000))[0]}

    @app.post("/api/jobs/{jid}/stop", dependencies=A)
    def job_stop(jid: str):
        try:
            return jobs_for(ctx.settings).stop(jid).info()
        except KeyError:
            raise HTTPException(404)

    # ------------------------------------------------------------------ schedule, audit, ledger
    @app.get("/api/schedule", dependencies=A)
    def schedule():
        return ScheduleStore(ctx.b / "schedule.json").load()

    @app.post("/api/schedule", dependencies=A)
    def schedule_add(body: dict):
        try:
            return ScheduleStore(ctx.b / "schedule.json").add(body["spec"], body["directive"], body.get("name", ""))
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/schedule/{tid}", dependencies=A)
    def schedule_remove(tid: str):
        return {"ok": ScheduleStore(ctx.b / "schedule.json").remove(tid)}

    @app.get("/api/reports", dependencies=A)
    def reports():
        folder = ctx.b / "reports"
        files = sorted(folder.glob("*.md"), reverse=True)[:50]
        return [{"name": f.name, "text": f.read_text(encoding="utf-8")[:6000]} for f in files]

    @app.get("/api/audit", dependencies=A)
    def audit(n: int = 80):
        ok, count, msg = blackbox.verify(ctx.b / "blackbox.jsonl")
        return {"ok": ok, "count": count, "message": msg, "entries": blackbox.BlackBox(ctx.b / "blackbox.jsonl").tail(n)}

    @app.get("/api/ledger", dependencies=A)
    def ledger():
        try:
            data = json.loads((ctx.b / "ledger.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        return {"days": sorted(data.items())[-30:], "cap": ctx.settings.budget_daily_usd}

    # ------------------------------------------------------------------ templates (saved directives)
    tpl_path = lambda: ctx.b / "templates.json"  # noqa: E731

    @app.get("/api/templates", dependencies=A)
    def templates():
        try:
            return json.loads(tpl_path().read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return [
                {"name": "Survey the sector", "text": "List the sandbox, read the README if there is one, and give me a short map of what's here."},
                {"name": "Run the tests", "text": "Find how this project runs its tests, run them, and report failures with the likely cause."},
                {"name": "Tidy up", "text": "Look for dead files, stray logs and obvious clutter. Propose deletions; don't delete anything yet."},
            ]

    @app.put("/api/templates", dependencies=A)
    def save_templates(body: list[dict]):
        clean = [{"name": str(t.get("name", ""))[:60], "text": str(t.get("text", ""))[:4000]} for t in body if t.get("text")]
        tpl_path().write_text(json.dumps(clean, indent=1))
        return clean

    # ------------------------------------------------------------------ files
    def _sb(path: str) -> Path:
        from ..permissions import Sandbox

        try:
            return Sandbox(ctx.settings.sandbox).resolve(path or ".")
        except SandboxViolation as e:
            raise HTTPException(400, str(e))

    @app.get("/api/files", dependencies=A)
    def files(path: str = "."):
        d = _sb(path)
        if not d.is_dir():
            raise HTTPException(404)
        root = ctx.settings.sandbox.resolve()
        out = []
        for p in sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if p.name in SKIP:
                continue
            st = p.stat()
            out.append({"name": p.name, "path": p.resolve().relative_to(root).as_posix(), "dir": p.is_dir(),
                        "size": st.st_size, "mtime": st.st_mtime})
        return {"path": d.relative_to(root).as_posix() if d != root else ".", "entries": out}

    @app.get("/api/file", dependencies=A)
    def file(path: str):
        f = _sb(path)
        if not f.is_file():
            raise HTTPException(404)
        raw = f.read_bytes()[:500_000]
        if b"\0" in raw[:4096]:
            return {"path": path, "binary": True, "size": f.stat().st_size}
        return {"path": path, "binary": False, "size": f.stat().st_size, "text": raw.decode("utf-8", "replace")}

    @app.post("/api/upload", dependencies=A)
    def upload(body: dict):
        name = re.sub(r"[^\w.\- ]", "_", Path(str(body.get("name", "file"))).name)[:120] or "file"
        try:
            data = base64.b64decode(body.get("data", ""), validate=True)
        except ValueError:
            raise HTTPException(400, "bad base64")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file over 20 MB")
        inbox = ctx.settings.sandbox / "_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / name
        stem, n = dest.stem, 1
        while dest.exists():
            dest = inbox / f"{stem}-{n}{dest.suffix}"
            n += 1
        dest.write_bytes(data)
        return {"path": dest.relative_to(ctx.settings.sandbox.resolve()).as_posix(), "size": len(data)}

    # ------------------------------------------------------------------ desktop-only
    if desktop is not None:
        from ..desktop.store import mask

        def ws_rows() -> list[dict]:
            active = str(ctx.settings.root.resolve())
            return [{**w, "active": w["path"] == active, "exists": Path(w["path"]).exists()} for w in desktop.store.data["workspaces"]]

        @app.get("/api/desktop/info", dependencies=A)
        def desktop_info():
            st = desktop.store
            key = st.get_api_key()
            return {"version": __version__, "platform": sys.platform, "setup_done": st.data["setup_done"] and bool(key),
                    "api_key": mask(key), "workspaces": ws_rows(), "default_workspace": str(desktop.default_workspace()),
                    "update": {**desktop.updater.snapshot(), "repo": st.update_repo, "auto_check": st.data["update"]["auto_check"]}}

        @app.post("/api/desktop/setup", dependencies=A)
        def desktop_setup(body: dict):
            key = str(body.get("api_key", "")).strip()
            if key:
                if not key.startswith("sk-ant-"):
                    raise HTTPException(400, "that doesn't look like an Anthropic API key (sk-ant-…)")
                where = desktop.store.set_api_key(key)
                os.environ["ANTHROPIC_API_KEY"] = key
            elif not desktop.store.get_api_key():
                raise HTTPException(400, "an API key is required")
            else:
                where = "unchanged"
            root = Path(str(body.get("workspace") or desktop.default_workspace())).expanduser()
            desktop.store.add_workspace(root, body.get("workspace_name", ""))
            desktop.store.data["active"] = str(root.resolve())
            s = ctx.switch(root.resolve())
            patch = {k: body[k] for k in ("model", "mode", "unit_name", "budget_session_usd", "budget_daily_usd") if body.get(k) not in (None, "")}
            if patch:
                new = Settings.model_validate({**s.model_dump(), **patch})
                new.root = s.root
                config.save(new)
                ctx.settings = new
            desktop.store.data["setup_done"] = True
            desktop.store.save()
            return {"ok": True, "key_stored_in": where}

        @app.post("/api/desktop/apikey", dependencies=A)
        def desktop_apikey(body: dict):
            key = str(body.get("api_key", "")).strip()
            if not key:
                desktop.store.forget_api_key()
                os.environ.pop("ANTHROPIC_API_KEY", None)
                return {"api_key": ""}
            if not key.startswith("sk-ant-"):
                raise HTTPException(400, "that doesn't look like an Anthropic API key (sk-ant-…)")
            desktop.store.set_api_key(key)
            os.environ["ANTHROPIC_API_KEY"] = key
            return {"api_key": mask(key)}

        @app.get("/api/desktop/workspaces", dependencies=A)
        def workspaces():
            return ws_rows()

        @app.post("/api/desktop/workspaces", dependencies=A)
        def add_ws(body: dict):
            root = Path(str(body.get("path", ""))).expanduser()
            if not str(root) or not root.is_absolute():
                raise HTTPException(400, "choose a folder")
            desktop.store.add_workspace(root, body.get("name", ""))
            if body.get("activate", True):
                desktop.store.data["active"] = str(root.resolve())
                desktop.store.save()
                ctx.switch(root.resolve())
            return ws_rows()

        @app.post("/api/desktop/workspaces/activate", dependencies=A)
        def activate_ws(body: dict):
            path = str(body.get("path", ""))
            if path not in [w["path"] for w in desktop.store.data["workspaces"]]:
                raise HTTPException(404)
            desktop.store.data["active"] = path
            desktop.store.save()
            ctx.switch(Path(path))
            return ws_rows()

        @app.delete("/api/desktop/workspaces", dependencies=A)
        def remove_ws(path: str):
            desktop.store.remove_workspace(path)  # forgets it; never deletes files
            if desktop.store.data["active"] and desktop.store.data["active"] != str(ctx.settings.root.resolve()):
                ctx.switch(Path(desktop.store.data["active"]))
            return ws_rows()

        @app.post("/api/desktop/pick-folder", dependencies=A)
        def pick_folder():
            return {"path": desktop.pick_folder() or ""}

        @app.post("/api/desktop/open", dependencies=A)
        def open_path(body: dict):
            target = body.get("target", "sandbox")
            p = {"sandbox": ctx.settings.sandbox, "workspace": ctx.settings.root, "reports": ctx.b / "reports"}.get(target)
            if p is None:
                p = _sb(str(body.get("path", ".")))
            desktop.open_path(p)
            return {"ok": True}

        @app.get("/api/desktop/update", dependencies=A)
        def update_state():
            return {**desktop.updater.snapshot(), "repo": desktop.store.update_repo,
                    "auto_check": desktop.store.data["update"]["auto_check"]}

        @app.post("/api/desktop/update/check", dependencies=A)
        def update_check():
            return desktop.updater.check()

        @app.post("/api/desktop/update/install", dependencies=A)
        def update_install():
            threading.Thread(target=desktop.updater.install, daemon=True).start()
            return desktop.updater.snapshot()

        @app.post("/api/desktop/update/skip", dependencies=A)
        def update_skip():
            desktop.store.data["update"]["skipped"] = desktop.updater.state.latest
            desktop.store.save()
            desktop.updater.state.skipped = True
            return desktop.updater.snapshot()

        @app.put("/api/desktop/update/prefs", dependencies=A)
        def update_prefs(body: dict):
            u = desktop.store.data["update"]
            if "auto_check" in body:
                u["auto_check"] = bool(body["auto_check"])
            if "repo" in body:
                repo = str(body["repo"]).strip()
                if repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
                    raise HTTPException(400, "use owner/name")
                u["repo"] = repo
            desktop.store.save()
            return update_state()

        desktop.updater.listeners.append(lambda snap: ctx.broadcast({"type": "update", "state": snap}))

    # ------------------------------------------------------------------ websocket
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, token: str = Query(""), session: str = Query("")):
        if not secrets.compare_digest(token, app.state.token):
            await ws.close(code=4401)
            return
        await ws.accept()
        bridge = Bridge(ws, asyncio.get_running_loop())
        ctx.bridges.add(bridge)
        sid = session if re.fullmatch(r"[\w-]*", session) else ""
        agent = Agent(ctx.settings.model_copy(deep=True), approver=bridge.approver, sink=bridge.sink, ask_fn=bridge.ask,
                      session_id=sid or None, surface="desktop" if ctx.desktop else "web", model=ctx.model)

        async def pump():
            while True:
                await ws.send_json(await bridge.out.get())

        pump_task = asyncio.create_task(pump())
        await ws.send_json({"type": "hello", "session": agent.session_id, "title": agent.title, "mode": agent.mode.value,
                            "model": agent.settings.model, "dry_run": agent.settings.dry_run,
                            "rows": transcript(agent.messages), "todos": agent.todos, "usd": agent.budget.meter.usd,
                            "tools": sorted(agent.tools)})
        try:
            while True:
                msg: dict[str, Any] = await ws.receive_json()
                t = msg.get("type")
                if t == "directive" and msg.get("text", "").strip():
                    def work(text=msg["text"], att=list(msg.get("attachments") or [])):
                        try:
                            agent.run(text, att)
                        except Exception as e:
                            bridge.send({"type": "event", "kind": "error", "data": {"message": str(e)}})
                            bridge.send({"type": "event", "kind": "done", "data": {"text": "", "steps": agent.steps, "usd": agent.budget.meter.usd}})
                    threading.Thread(target=work, daemon=True).start()
                elif t == "approval":
                    bridge.answer(msg["id"], msg)
                elif t == "answer":
                    bridge.answer("question", msg)
                elif t == "interrupt":
                    agent.interrupt()
                elif t == "mode" and msg.get("mode") in Mode.__members__:
                    agent.set_mode(msg["mode"])
                    bridge.send({"type": "mode", "mode": agent.mode.value})
                elif t == "model" and re.fullmatch(r"claude-[\w.-]+", str(msg.get("model", ""))):
                    agent.settings.model = msg["model"]
                    bridge.send({"type": "model", "model": agent.settings.model})
                elif t == "dry":
                    agent.settings.dry_run = bool(msg.get("on"))
                    bridge.send({"type": "dry", "on": agent.settings.dry_run})
                elif t == "undo":
                    r = agent.checkpoints.undo()
                    bridge.send({"type": "event", "kind": "status",
                                 "data": {"message": f"restored {', '.join(r['restored'])}" if r["id"] else "nothing to undo"}})
        except WebSocketDisconnect:
            pass
        finally:
            ctx.bridges.discard(bridge)
            agent.interrupt()
            for key in list(bridge.pending):
                bridge.answer(key, {"approved": False, "note": "operator disconnected"})
            pump_task.cancel()

    return app
