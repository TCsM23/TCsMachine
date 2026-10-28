"""Web terminal and Telegram gateway, with the fake model and no network."""

import threading
import time

import pytest

from pale.config import Mode
from pale.testing import FakeModel, call, say


def test_web_requires_token_and_runs_a_directive(settings):
    from fastapi.testclient import TestClient
    from pale.web.server import create_app

    settings.mode = Mode.ask
    model = FakeModel([call("write_file", {"path": "w.txt", "content": "from web"}), say("written")])
    app = create_app(settings, model=model)
    client = TestClient(app)
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"X-Pale-Token": app.state.token}).json()["mode"] == "ask"

    with client.websocket_connect(f"/ws?token={app.state.token}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        ws.send_json({"type": "directive", "text": "write it"})
        kinds = []
        while True:
            m = ws.receive_json()
            if m["type"] == "approval":
                assert m["risk"] == "low" and "+from web" in m["preview"]
                ws.send_json({"type": "approval", "id": m["id"], "approved": True})
            elif m["type"] == "event":
                kinds.append(m["kind"])
                if m["kind"] == "done":
                    break
    assert (settings.sandbox / "w.txt").read_text() == "from web"
    assert "tool_result" in kinds
    sessions = client.get("/api/sessions", headers={"X-Pale-Token": app.state.token}).json()
    assert sessions and sessions[0]["title"] == "write it"


def test_web_rejects_bad_token(settings):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from pale.web.server import create_app

    client = TestClient(create_app(settings, model=FakeModel([])))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=wrong") as ws:
            ws.receive_json()


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def __call__(self, method, **params):
        self.sent.append((method, params))
        return {"message_id": len(self.sent)}


def _wait(pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_telegram_whitelist_and_button_approval(settings, monkeypatch):
    from pale.gateway.telegram import TelegramGateway

    monkeypatch.setenv("PALE_TELEGRAM_TOKEN", "123:abc")
    settings.telegram.allowed_user_ids = [42]
    settings.mode = Mode.ask
    model = FakeModel([call("write_file", {"path": "t.txt", "content": "from phone"}), say("done from phone")])
    gw = TelegramGateway(settings, model=model)
    fake = FakeTelegram()
    gw.call = fake

    gw.handle({"update_id": 1, "message": {"from": {"id": 666}, "chat": {"id": 666}, "text": "rm everything"}})
    assert fake.sent == []  # stranger ignored

    gw.handle({"update_id": 2, "message": {"from": {"id": 42}, "chat": {"id": 42}, "text": "write t.txt"}})
    assert _wait(lambda: any("AUTHORIZATION" in p.get("text", "") for _, p in fake.sent))
    chat = gw.chats[42]
    key = next(iter(chat.waiting))
    gw.handle({"update_id": 3, "callback_query": {"id": "cq", "from": {"id": 42}, "data": f"ap:{key}:y",
                                                  "message": {"chat": {"id": 42}, "message_id": 9}}})
    assert _wait(lambda: any("done from phone" in p.get("text", "") for _, p in fake.sent))
    assert (settings.sandbox / "t.txt").read_text() == "from phone"


def test_telegram_refuses_without_allowlist(settings, monkeypatch):
    from pale.gateway.telegram import TelegramGateway

    monkeypatch.setenv("PALE_TELEGRAM_TOKEN", "123:abc")
    with pytest.raises(SystemExit):
        TelegramGateway(settings)


def test_unattended_run_never_escalates(settings):
    from pale.agent import Agent
    from pale.events import deny_all

    settings.mode = Mode.ask
    model = FakeModel([call("run_command", {"command": "echo hi"}), say("could not run")])
    agent = Agent(settings, model=model, approver=deny_all, unattended=True)
    agent.run("scheduled job")
    assert "ask_operator" not in agent.tools
    res = model.requests[-1]["messages"][-1]["content"][0]
    assert res["is_error"] and "unattended" in res["content"]
