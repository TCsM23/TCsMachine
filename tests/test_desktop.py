"""Desktop app pieces: updater, app store, desktop endpoints, MCP, git tools, attachments, settings."""

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import textwrap

import httpx
import pytest

from pale import config
from pale.config import Mode
from pale.desktop import updater as upd
from pale.desktop.store import Store
from pale.testing import FakeModel, call, say

from conftest import last_results


# ------------------------------------------------------------------ updater

def test_version_compare():
    assert upd.newer("v9.0.0") and not upd.newer("v0.0.1")
    assert upd.newer("v1.10.0", "1.9.9") and not upd.newer("v1.2.3", "1.2.3")
    assert upd.parse_version("nightly") is None and not upd.newer("nightly")


def fake_github(tag="v9.9.9", installer=b"MZ fake installer", sums_ok=True, with_sums=True, status=200):
    name = f"PALE-Setup-{tag[1:]}.exe" if sys.platform == "win32" else (
        f"PALE-{tag[1:]}-macos.zip" if sys.platform == "darwin" else f"PALE-{tag[1:]}-linux.tar.gz")
    digest = hashlib.sha256(installer if sums_ok else b"other").hexdigest()
    assets = [{"name": name, "size": len(installer), "browser_download_url": "https://dl/" + name}]
    if with_sums:
        assets.append({"name": "SHA256SUMS.txt", "size": 1, "browser_download_url": "https://dl/sums"})

    def handler(req: httpx.Request):
        if req.url.host == "api.github.com":
            if status != 200:
                return httpx.Response(status)
            return httpx.Response(200, json={"tag_name": tag, "body": "## notes\n- faster", "html_url": "https://gh/rel",
                                             "published_at": "2026-10-30T00:00:00Z", "assets": assets})
        if req.url.path == "/sums":
            return httpx.Response(200, text=f"{digest}  {name}\n")
        return httpx.Response(200, content=installer)

    return httpx.Client(transport=httpx.MockTransport(handler)), name


def make_updater(tmp_path, **kw):
    client, name = fake_github(**kw)
    return upd.Updater(lambda: "me/pale", tmp_path, http=client), name


def test_check_finds_newer_release(tmp_path):
    u, name = make_updater(tmp_path)
    s = u.check()
    assert s["status"] == "available" and s["latest"] == "9.9.9" and s["asset"] == name and "faster" in s["notes"]


def test_check_current_and_missing(tmp_path):
    u, _ = make_updater(tmp_path, tag="v0.0.1")
    assert u.check()["status"] == "current"
    u, _ = make_updater(tmp_path, status=404)
    assert u.check()["status"] == "current"
    u = upd.Updater(lambda: "", tmp_path)
    assert u.check()["status"] == "error"


def test_download_verifies_checksum(tmp_path):
    u, name = make_updater(tmp_path)
    u.check()
    path = u.download()
    assert path.read_bytes() == b"MZ fake installer" and u.state.status == "ready"


def test_download_rejects_tampered_file(tmp_path):
    u, _ = make_updater(tmp_path, sums_ok=False)
    u.check()
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        u.download()
    assert not list((tmp_path / "updates").glob("*.exe"))


def test_download_refuses_without_checksums(tmp_path):
    u, _ = make_updater(tmp_path, with_sums=False)
    u.check()
    with pytest.raises(RuntimeError, match="unverifiable"):
        u.download()


def test_install_from_source_opens_release_page(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(upd.webbrowser, "open", opened.append)
    u, _ = make_updater(tmp_path)
    u.check()
    u.state.self_install = False
    u.install()
    assert opened == ["https://gh/rel"]


# ------------------------------------------------------------------ store

def test_store_workspaces_and_key_file(tmp_path, monkeypatch):
    st = Store(tmp_path)
    monkeypatch.setattr(st, "_keyring", lambda: None)
    st.add_workspace(tmp_path / "a")
    st.add_workspace(tmp_path / "a")  # no duplicates
    st.add_workspace(tmp_path / "b", "bee")
    assert [w["name"] for w in Store(tmp_path).data["workspaces"]] == ["a", "bee"]
    assert st.set_api_key("sk-ant-test-123456789") == "file"
    assert st.get_api_key() == "sk-ant-test-123456789"
    st.forget_api_key()
    assert st.get_api_key() == ""


# ------------------------------------------------------------------ desktop endpoints

class FakeDesktop:
    def __init__(self, tmp_path, monkeypatch):
        self.store = Store(tmp_path / "appdata")
        monkeypatch.setattr(self.store, "_keyring", lambda: None)
        self.updater = upd.Updater(lambda: "me/pale", tmp_path / "appdata")
        self.opened = []
        self.default_workspace = lambda: tmp_path / "sector-01"

    def pick_folder(self):
        return None

    def open_path(self, p):
        self.opened.append(p)


@pytest.fixture
def desk(settings, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from pale.web.server import create_app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    d = FakeDesktop(tmp_path, monkeypatch)
    model = FakeModel([])
    app = create_app(settings, model=model, desktop=d)
    client = TestClient(app)
    client.headers["X-Pale-Token"] = app.state.token
    return client, d, model, app


def test_setup_wizard_flow(desk, tmp_path):
    client, d, _, app = desk
    assert client.get("/api/desktop/info").json()["setup_done"] is False
    r = client.post("/api/desktop/setup", json={"api_key": "nope"})
    assert r.status_code == 400
    ws = tmp_path / "my-sector"
    r = client.post("/api/desktop/setup", json={"api_key": "sk-ant-abcdefghijklmnop", "workspace": str(ws),
                                                "model": "claude-haiku-5-5", "mode": "edits", "unit_name": "K-9"})
    assert r.status_code == 200, r.text
    info = client.get("/api/desktop/info").json()
    assert info["setup_done"] and info["api_key"].startswith("sk-ant-")
    assert (ws / "sandbox").is_dir() and (ws / "pale.toml").exists()
    st = client.get("/api/state").json()
    assert st["root"] == str(ws.resolve()) and st["unit"] == "K-9" and st["model"] == "claude-haiku-5-5"
    assert config.load(ws).mode == Mode.edits


def test_workspace_switching(desk, tmp_path):
    client, d, _, _ = desk
    a, b = tmp_path / "wa", tmp_path / "wb"
    client.post("/api/desktop/workspaces", json={"path": str(a)})
    rows = client.post("/api/desktop/workspaces", json={"path": str(b)}).json()
    assert [r["active"] for r in rows] == [False, True]
    rows = client.post("/api/desktop/workspaces/activate", json={"path": str(a.resolve())}).json()
    assert client.get("/api/state").json()["root"] == str(a.resolve())
    assert client.post("/api/desktop/workspaces", json={"path": "relative/path"}).status_code == 400


def test_settings_roundtrip(desk, settings):
    client, *_ = desk
    s = client.get("/api/settings").json()
    s.update(max_steps=7, allow=["run_command(make*)"], web_search=True)
    assert client.put("/api/settings", json=s).status_code == 200
    again = config.load(settings.root)
    assert again.max_steps == 7 and again.allow == ["run_command(make*)"] and again.web_search
    assert client.put("/api/settings", json={"mode": "chaos"}).status_code == 400


def test_upload_and_image_attachment_reach_the_model(desk, settings):
    client, d, model, app = desk
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    r = client.post("/api/upload", json={"name": "../shot.png", "data": base64.b64encode(png).decode()}).json()
    assert r["path"] == "_inbox/shot.png"
    model.script.append(say("I see a pixel."))
    with client.websocket_connect(f"/ws?token={app.state.token}") as ws:
        ws.receive_json()
        ws.send_json({"type": "directive", "text": "what is this?", "attachments": [r["path"]]})
        while True:
            m = ws.receive_json()
            if m.get("kind") == "done":
                break
    content = model.requests[-1]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert "_inbox/shot.png" in content[1]["text"]


def test_files_and_export(desk, settings):
    client, *_ = desk
    (settings.sandbox / "dir").mkdir()
    (settings.sandbox / "dir" / "a.txt").write_text("hello")
    listing = client.get("/api/files").json()
    assert any(e["name"] == "dir" and e["dir"] for e in listing["entries"])
    assert client.get("/api/file", params={"path": "dir/a.txt"}).json()["text"] == "hello"
    assert client.get("/api/file", params={"path": "../../etc/passwd"}).status_code == 400
    from pale.agent import Agent

    a = Agent(settings, model=FakeModel([say("done")]))
    a.run("export me")
    md = client.get(f"/api/sessions/{a.session_id}/export").text
    assert "export me" in md and "**Unit:**" in md
    assert client.delete(f"/api/sessions/{a.session_id}").json()["ok"]


def test_update_endpoints(desk):
    client, d, *_ = desk
    r = client.put("/api/desktop/update/prefs", json={"repo": "bad repo"})
    assert r.status_code == 400
    r = client.put("/api/desktop/update/prefs", json={"repo": "someone/thing", "auto_check": False}).json()
    assert r["repo"] == "someone/thing" and r["auto_check"] is False


# ------------------------------------------------------------------ MCP

FAKE_MCP = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        m, rid = msg["method"], msg["id"]
        if m == "initialize":
            res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "echo"}}
        elif m == "tools/list":
            res = {"tools": [{"name": "shout", "description": "uppercase text",
                              "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
        elif m == "tools/call":
            res = {"content": [{"type": "text", "text": msg["params"]["arguments"]["text"].upper()}]}
        else:
            res = {}
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}), flush=True)
''')


def test_mcp_tools_are_gated_and_work(settings, make_agent, tmp_path):
    from pale import mcp
    from pale.events import ApprovalDecision

    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_MCP)
    settings.mcp = {"echo": config.MCPServer(command=sys.executable, args=[str(script)])}
    settings.mode = Mode.ask
    asked = []
    model = FakeModel([call("mcp__echo__shout", {"text": "glory"}), say("ok")])
    try:
        agent = make_agent(model, approver=lambda r: asked.append(r) or ApprovalDecision(True))
        assert "mcp__echo__shout" in agent.tools
        agent.run("shout")
        assert asked and asked[0].tool == "mcp__echo__shout"
        assert last_results(model)[0]["content"] == "GLORY"
        sent = {t["name"]: t for t in model.requests[0]["tools"]}
        assert sent["mcp__echo__shout"]["input_schema"]["properties"]["text"]["type"] == "string"
    finally:
        mcp.shutdown(settings)


def test_mcp_bad_server_is_reported_not_fatal(settings, make_agent):
    from pale import mcp

    settings.mcp = {"ghost": config.MCPServer(command="definitely-not-a-real-binary-xyz")}
    model = FakeModel([say("ok")])
    try:
        agent = make_agent(model)
        events = []
        agent.sink = lambda ev: events.append(ev)
        agent.run("hi")
        assert any("ghost" in e.data.get("message", "") for e in events if e.kind == "warning")
    finally:
        mcp.shutdown(settings)


# ------------------------------------------------------------------ git, web search, config

@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_git_tools(settings, make_agent):
    subprocess.run(["git", "init", "-q"], cwd=settings.sandbox, check=True)
    (settings.sandbox / "a.txt").write_text("one")
    model = FakeModel([
        call("git_status", {}),
        call("git_commit", {"message": "first"}),
        call("git_log", {"n": 5}),
        say("ok"),
    ])
    make_agent(model).run("commit it")
    assert "a.txt" in model.requests[1]["messages"][-1]["content"][0]["content"]
    assert "first" in last_results(model)[0]["content"]


def test_web_search_tool_is_requested_only_when_enabled(settings, make_agent):
    model = FakeModel([say("a"), say("b")])
    agent = make_agent(model)
    agent.run("x")
    assert not any(t.get("type", "").startswith("web_search") for t in model.requests[0]["tools"])
    agent.settings.web_search = True
    agent.run("y")
    ws = [t for t in model.requests[1]["tools"] if t.get("name") == "web_search"]
    assert ws and ws[0]["type"] == "web_search_20260209" and "eager_input_streaming" not in ws[0]


def test_config_save_roundtrip(settings):
    settings.allow = ['run_command(echo "hi"*)']
    settings.mcp = {"fs": config.MCPServer(command="npx", args=["-y", "server"], env={"A": "1"})}
    config.save(settings)
    again = config.load(settings.root)
    assert again.allow == settings.allow and again.mcp["fs"].args == ["-y", "server"]
