"""Local web terminal: FastAPI + one WebSocket per open tab.

Security: binds to 127.0.0.1 by default, and every request needs the random token printed at
startup (a page on another site can't guess it, so it can't drive your agent). The agent runs in a
worker thread; approvals block that thread until the browser answers.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .. import blackbox
from ..agent import Agent
from ..config import Mode, Settings
from ..events import ApprovalDecision, ApprovalRequest, Event
from ..memory import Memory, Skills
from ..runtime import list_sessions, transcript
from ..scheduler import ScheduleStore

STATIC = Path(__file__).parent / "static"


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
        ans = self._wait("question", {"type": "question", "text": question})
        return ans.get("text", "")

    def answer(self, key: str, payload: dict) -> None:
        if key in self.pending:
            done, box = self.pending.pop(key)
            box.update(payload)
            done.set()


def create_app(settings: Settings, model=None) -> FastAPI:
    """`model` overrides the ModelClient (tests pass a FakeModel)."""
    settings.ensure_dirs()
    app = FastAPI(title="PALE", docs_url=None, redoc_url=None)
    app.state.token = secrets.token_urlsafe(18)
    b = settings.bunker

    def auth(x_pale_token: str = Header("")):
        if not secrets.compare_digest(x_pale_token, app.state.token):
            raise HTTPException(401, "bad token")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/static/{name}")
    def static(name: str):
        p = (STATIC / name).resolve()
        if p.parent != STATIC.resolve() or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        return {"unit": settings.unit_name, "model": settings.model, "mode": settings.mode.value,
                "dry_run": settings.dry_run, "sandbox": str(settings.sandbox),
                "budget": {"session": settings.budget_session_usd, "daily": settings.budget_daily_usd}}

    @app.get("/api/sessions", dependencies=[Depends(auth)])
    def sessions():
        return list_sessions(settings)

    @app.get("/api/sessions/{sid}", dependencies=[Depends(auth)])
    def session(sid: str):
        p = b / "sessions" / f"{Path(sid).name}.json"
        if not p.exists():
            raise HTTPException(404)
        d = json.loads(p.read_text())
        return {"id": d["id"], "title": d.get("title"), "todos": d.get("todos", []), "rows": transcript(d["messages"])}

    @app.get("/api/memory", dependencies=[Depends(auth)])
    def memory():
        return [{"name": n.name, "description": n.description, "body": n.body} for n in Memory(b / "memory").all()]

    @app.delete("/api/memory/{name}", dependencies=[Depends(auth)])
    def forget(name: str):
        return {"ok": Memory(b / "memory").delete(name)}

    @app.get("/api/skills", dependencies=[Depends(auth)])
    def skills():
        sk = Skills(b / "skills")
        row = lambda n, q: {"name": n.name, "description": n.description, "body": n.body, "quarantined": q}  # noqa: E731
        return [row(n, False) for n in sk.all()] + [row(n, True) for n in sk.quarantine.all()]

    @app.post("/api/skills/{name}/{action}", dependencies=[Depends(auth)])
    def skill_action(name: str, action: str):
        sk = Skills(b / "skills")
        if action == "approve":
            try:
                sk.approve(name)
            except KeyError:
                raise HTTPException(404)
            return {"ok": True}
        if action == "reject":
            return {"ok": sk.reject(name)}
        raise HTTPException(400)

    @app.get("/api/schedule", dependencies=[Depends(auth)])
    def schedule():
        return ScheduleStore(b / "schedule.json").load()

    @app.post("/api/schedule", dependencies=[Depends(auth)])
    def schedule_add(body: dict):
        try:
            return ScheduleStore(b / "schedule.json").add(body["spec"], body["directive"], body.get("name", ""))
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/schedule/{tid}", dependencies=[Depends(auth)])
    def schedule_remove(tid: str):
        return {"ok": ScheduleStore(b / "schedule.json").remove(tid)}

    @app.get("/api/audit", dependencies=[Depends(auth)])
    def audit(n: int = 60):
        ok, count, msg = blackbox.verify(b / "blackbox.jsonl")
        return {"ok": ok, "count": count, "message": msg, "entries": blackbox.BlackBox(b / "blackbox.jsonl").tail(n)}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, token: str = Query(""), session: str = Query("")):
        if not secrets.compare_digest(token, app.state.token):
            await ws.close(code=4401)
            return
        await ws.accept()
        bridge = Bridge(ws, asyncio.get_running_loop())
        agent = Agent(settings.model_copy(), approver=bridge.approver, sink=bridge.sink, ask_fn=bridge.ask,
                      session_id=session or None, surface="web", model=model)

        async def pump():
            while True:
                await ws.send_json(await bridge.out.get())

        pump_task = asyncio.create_task(pump())
        await ws.send_json({"type": "hello", "session": agent.session_id, "mode": agent.mode.value,
                            "dry_run": agent.settings.dry_run, "rows": transcript(agent.messages), "todos": agent.todos,
                            "usd": agent.budget.meter.usd})
        try:
            while True:
                msg = await ws.receive_json()
                t = msg.get("type")
                if t == "directive" and msg.get("text", "").strip():
                    def work(text=msg["text"]):
                        try:
                            agent.run(text)
                        except Exception as e:
                            bridge.send({"type": "event", "kind": "error", "data": {"message": str(e)}})
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
            agent.interrupt()
            for key in list(bridge.pending):
                bridge.answer(key, {"approved": False, "note": "operator disconnected"})
            pump_task.cancel()

    return app
