"""v0.3 features: plan mode, specialist units, loop guard, background jobs, watchers, rewind, review."""

import json
import sys
import time
from datetime import datetime

import pytest

from pale.agent import Agent
from pale.config import Mode
from pale.events import ApprovalDecision
from pale.permissions import Policy, Risk
from pale.runtime import directive_turns, fork_session, search_sessions, transcript, unattended_run
from pale.scheduler import Scheduler, ScheduleStore, watch_globs
from pale.testing import FakeModel, call, say

from conftest import last_results


class Recorder:
    def __init__(self, answers=None):
        self.reqs, self.answers = [], list(answers or [])

    def __call__(self, req):
        self.reqs.append(req)
        return self.answers.pop(0) if self.answers else ApprovalDecision(True)


# ------------------------------------------------------------------ plan mode

def test_plan_mode_blocks_writes_until_the_plan_is_approved(make_agent, settings):
    settings.mode, settings.plan_then = Mode.plan, Mode.edits
    plan = {"summary": "## Approach\nAdd a greeting file.", "steps": ["write hello.txt", "verify"]}
    model = FakeModel([
        call("write_file", {"path": "hello.txt", "content": "hi"}),  # refused: no plan yet
        call("submit_plan", plan),
        call("write_file", {"path": "hello.txt", "content": "hi"}),  # edits mode now: pre-approved
        say("done"),
    ])
    rec = Recorder()
    agent = make_agent(model, approver=rec)
    agent.run("greet")
    assert (settings.sandbox / "hello.txt").read_text() == "hi"
    assert [r.tool for r in rec.reqs] == ["submit_plan"]  # only the plan needed the operator
    assert "**Steps**" in rec.reqs[0].preview and "1. write hello.txt" in rec.reqs[0].preview
    assert agent.mode == Mode.edits
    assert agent.todos[0] == {"text": "write hello.txt", "status": "active"}
    first = model.requests[1]["messages"][-1]["content"][0]
    assert first["is_error"] and "plan mode" in first["content"]


def test_declined_plan_returns_the_note_and_stays_in_plan_mode(make_agent, settings):
    settings.mode = Mode.plan
    model = FakeModel([call("submit_plan", {"summary": "rewrite everything", "steps": ["rm"]}), say("revising")])
    agent = make_agent(model, approver=Recorder([ApprovalDecision(False, "too broad, only touch src/")]))
    agent.run("fix it")
    (res,) = last_results(model)
    assert res["is_error"] and "too broad, only touch src/" in res["content"]
    assert agent.mode == Mode.plan


def test_submit_plan_is_only_offered_in_plan_mode(make_agent, settings):
    model = FakeModel([say("a"), say("b")])
    agent = make_agent(model)
    agent.run("x")
    assert "submit_plan" not in [t["name"] for t in model.requests[0]["tools"]]
    agent.set_mode("plan")
    agent.run("y")
    assert "submit_plan" in [t["name"] for t in model.requests[1]["tools"]]
    pol = Policy(settings)
    pol.mode = Mode.autonomous
    assert pol.decide("submit_plan", "p", True, Risk("low")).verdict == "deny"


def test_plan_needs_the_operator_even_in_dry_run(make_agent, settings):
    settings.mode, settings.dry_run = Mode.plan, True
    rec = Recorder()
    agent = make_agent(FakeModel([call("submit_plan", {"summary": "s", "steps": ["a"]}), say("ok")]), approver=rec)
    agent.run("x")
    assert [r.tool for r in rec.reqs] == ["submit_plan"]


def test_unattended_runs_never_leave_plan_mode(settings, monkeypatch):
    settings.mode = Mode.plan
    seen = {}

    class Spy(Agent):
        def run(self, directive, attachments=None):
            seen["mode"], seen["directive"] = self.mode, directive
            return "ok"

    monkeypatch.setattr("pale.runtime.Agent", Spy)
    unattended_run(settings, {"id": "t1", "name": "n", "directive": "d", "changed": ["a.py"]})
    assert seen["mode"] == Mode.observe
    assert "Triggered by changes to: a.py" in seen["directive"]


# ------------------------------------------------------------------ loop guard

def test_loop_guard_warns_then_halts(make_agent, settings):
    (settings.sandbox / "a.txt").write_text("same")
    model = FakeModel([call("read_file", {"path": "a.txt"}) for _ in range(6)] + [say("never")])
    agent = make_agent(model)
    out = agent.run("read")
    assert out.startswith("[halted] loop guard")
    third = model.requests[3]["messages"][-1]["content"][0]["content"]
    assert third.startswith("[loop guard]")
    assert len(model.requests) == 5  # the 5th identical call is refused and the directive stops
    # history stays valid: the refused call still got a result
    assert agent.messages[-1]["content"][0]["type"] == "tool_result"


def test_loop_guard_ignores_polling(make_agent, settings):
    model = FakeModel([call("job_status", {}) for _ in range(6)] + [say("done")])
    assert make_agent(model).run("poll") == "done"


# ------------------------------------------------------------------ background jobs

def test_background_job_runs_and_reports(make_agent, settings):
    code = "import time; print('ready', flush=True); time.sleep(0.3); print('bye')"
    cmd = f'"{sys.executable}" -c "{code}"'
    model = FakeModel([call("run_command", {"command": cmd, "background": True}), say("started")])
    events = []
    agent = make_agent(model, sink=lambda ev: events.append(ev))
    agent.run("start it")
    started = last_results(model)[0]["content"]
    assert "started background job" in started
    jid = started.split("job ")[1].split(":")[0]
    from pale.jobs import jobs_for

    jobs = jobs_for(settings)
    for _ in range(50):
        if not jobs.get(jid).running:
            break
        time.sleep(0.1)
    assert jobs.get(jid).code == 0
    model.script = [call("job_status", {"id": jid}), say("ok")]
    agent.run("check")
    out = last_results(model)[0]["content"]
    assert "exited with code 0" in out and "ready" in out and "bye" in out
    assert any(e.kind == "job" for e in events)


def test_background_job_stop_and_limit(make_agent, settings):
    settings.max_jobs = 1
    cmd = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    model = FakeModel([call("run_command", {"command": cmd, "background": True}),
                       call("run_command", {"command": cmd + " ", "background": True}), say("x")])
    agent = make_agent(model)
    agent.run("two")
    second = last_results(model)[0]
    assert second["is_error"] and "already running" in second["content"]
    from pale.jobs import jobs_for

    jobs = jobs_for(settings)
    (job,) = jobs.running()
    model.script = [call("job_stop", {"id": job.id}), say("stopped")]
    agent.run("stop")
    assert not job.running and job.stopped == "operator"


def test_background_job_is_still_approval_gated(make_agent, settings):
    settings.mode = Mode.observe
    model = FakeModel([call("run_command", {"command": "sleep 5", "background": True}), say("x")])
    make_agent(model).run("go")
    assert last_results(model)[0]["is_error"]


# ------------------------------------------------------------------ specialist units

def _unit(settings, name, meta, body="Do the thing."):
    settings.units_dir.mkdir(exist_ok=True)
    head = "\n".join(f"{k}: {v}" for k, v in {"name": name, **meta}.items())
    (settings.units_dir / f"{name}.md").write_text(f"---\n{head}\n---\n{body}\n")


def test_specialist_unit_is_listed_and_dispatched(make_agent, settings):
    _unit(settings, "scout", {"description": "maps the codebase", "model": "claude-sonnet-5-5", "tools": "list_dir, write_file, dispatch_unit"},
          "You map codebases.")
    (settings.sandbox / "a.py").write_text("x")

    def sub_turn(req):
        assert req["model"] == "claude-sonnet-5-5"
        assert "You map codebases." in str(req["system"])
        assert sorted(t["name"] for t in req["tools"]) == ["list_dir", "write_file"]  # never dispatch_unit
        return call("list_dir", {"path": "."})

    model = FakeModel([
        lambda req: (("scout: maps the codebase" in str(req["system"])) or pytest.fail("roster missing")) and
        call("dispatch_unit", {"task": "map it", "unit": "scout"}),
        sub_turn, say("a.py only"), say("the scout found a.py"),
    ])
    agent = make_agent(model)
    assert agent.run("map") == "the scout found a.py"
    assert "scout unit report" in last_results(model)[0]["content"]


def test_specialist_write_tools_go_through_the_parent_approver(make_agent, settings):
    settings.mode = Mode.ask
    _unit(settings, "fixer", {"description": "fixes", "tools": "write_file"})
    model = FakeModel([call("dispatch_unit", {"task": "fix", "unit": "fixer"}),
                       call("write_file", {"path": "f.txt", "content": "fixed"}), say("fixed it"), say("done")])
    rec = Recorder([ApprovalDecision(False, "no")])
    agent = make_agent(model, approver=rec)
    agent.run("fix")
    assert [r.tool for r in rec.reqs] == ["write_file"]
    assert not (settings.sandbox / "f.txt").exists()


def test_specialist_loses_write_tools_in_observe_mode(make_agent, settings):
    settings.mode = Mode.observe
    _unit(settings, "fixer", {"description": "fixes", "tools": "write_file, read_file"})
    seen = {}

    def sub(req):
        seen["tools"] = [t["name"] for t in req["tools"]]
        return say("nothing")

    model = FakeModel([call("dispatch_unit", {"task": "fix", "unit": "fixer"}), sub, say("done")])
    make_agent(model).run("fix")
    assert seen["tools"] == ["read_file"]


def test_unknown_unit_is_an_error(make_agent):
    model = FakeModel([call("dispatch_unit", {"task": "x", "unit": "ghost"}), say("ok")])
    make_agent(model).run("x")
    assert "no specialist unit named ghost" in last_results(model)[0]["content"]


# ------------------------------------------------------------------ review pass

def test_review_pass_reports_findings_into_the_next_turn(make_agent, settings):
    settings.review = True
    (settings.sandbox / "m.py").write_text("def f(x):\n    return x\n")

    def reviewer(req):
        task = req["messages"][0]["content"][-1]["text"]
        assert "-    return x" in task and "+    return x + 1" in task
        assert all(not t.get("name", "").startswith(("write", "edit", "run")) for t in req["tools"])
        return say("m.py:2: off by one. use x.")

    model = FakeModel([call("read_file", {"path": "m.py"}),
                       call("edit_file", {"path": "m.py", "old_string": "return x", "new_string": "return x + 1"}),
                       say("edited"), reviewer, say("ack")])
    events = []
    agent = make_agent(model, sink=events.append)
    agent.run("increment")
    (rev,) = [e for e in events if e.kind == "review"]
    assert not rev.data["clean"] and "off by one" in rev.data["text"]
    agent.run("next")
    assert "[review unit findings" in json.dumps(model.requests[-1]["messages"][-1])


def test_review_skips_directives_without_changes(make_agent, settings):
    settings.review = True
    model = FakeModel([say("nothing to do")])
    make_agent(model).run("look")
    assert len(model.requests) == 1


# ------------------------------------------------------------------ rewind & search

def test_rewind_forks_before_a_directive(make_agent, settings):
    model = FakeModel([call("list_dir", {"path": "."}), say("one"), say("two"), say("three")])
    agent = make_agent(model)
    agent.run("first")
    agent.run("second")
    turns = directive_turns(agent.messages)
    assert len(turns) == 2
    rows = transcript(agent.messages)
    assert [r["turn"] for r in rows if r["who"] == "user" and r["kind"] == "text"] == [0, 1]
    r = fork_session(settings, agent.session_id, 1)
    assert r["directive"] == "second"
    forked = Agent(settings, model=model, session_id=r["id"])
    assert forked.messages == agent.messages[:turns[1]]
    assert forked.run("second, differently") == "three"
    with pytest.raises(IndexError):
        fork_session(settings, agent.session_id, 5)


def test_rewind_keeps_tool_results_that_shared_the_message(make_agent, settings):
    agent = make_agent(FakeModel([]))
    agent.messages = [
        {"role": "user", "content": [{"type": "text", "text": "a"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "list_dir", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "halted"},
                                     {"type": "text", "text": "b"}]},
    ]
    agent.save()
    r = fork_session(settings, agent.session_id, 1)
    msgs = json.loads((settings.bunker / "sessions" / f"{r['id']}.json").read_text())["messages"]
    assert msgs[-1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "halted"}]}


def test_search_sessions(make_agent, settings):
    make_agent(FakeModel([say("the pump is on tower four")])).run("status report")
    make_agent(FakeModel([say("nothing")])).run("other")
    hits = search_sessions(settings, "TOWER FOUR")
    assert len(hits) == 1 and "tower four" in hits[0]["snippet"]


# ------------------------------------------------------------------ watchers

def test_watch_spec_parsing_and_safety(tmp_path):
    assert watch_globs("watch src/**/*.py, tests/*.py") == ["src/**/*.py", "tests/*.py"]
    assert watch_globs("every 5m") is None
    with pytest.raises(ValueError):
        watch_globs("watch ../secrets/*")
    store = ScheduleStore(tmp_path / "s.json")
    t = store.add("watch *.txt", "summarize the change")
    assert t["watch"] == ["*.txt"] and t["next"] is None
    assert store.due(datetime.now()) == []


def test_watcher_fires_on_change_and_ignores_its_own_edits(tmp_path):
    root = tmp_path / "sb"
    root.mkdir()
    (root / "a.txt").write_text("1")
    store = ScheduleStore(tmp_path / "s.json")
    store.add("watch *.txt", "react")
    fired = []

    def run(task):
        fired.append(task["changed"])
        (root / "a.txt").write_text("edited by the run")  # must not retrigger
        return "ok"

    sched = Scheduler(store, run, watch_root=root)
    sched.poll()  # baseline
    sched.poll()
    assert fired == []
    time.sleep(0.02)
    (root / "b.txt").write_text("new")
    sched.poll()
    for _ in range(50):
        if fired and not sched._running:
            break
        time.sleep(0.02)
    assert fired == [["b.txt"]]
    sched._fires.clear()  # skip the cooldown
    sched.poll()
    assert len(fired) == 1


# ------------------------------------------------------------------ web endpoints

def test_web_units_jobs_fork_search(settings):
    from fastapi.testclient import TestClient
    from pale.web.server import create_app

    app = create_app(settings, model=FakeModel([say("hello tower")]))
    c = TestClient(app)
    H = {"X-Pale-Token": app.state.token}
    assert c.put("/api/units/Test Runner", json={"description": "runs tests", "tools": "run_command", "body": "Run them."}, headers=H).json()["name"] == "test-runner"
    (u,) = c.get("/api/units", headers=H).json()
    assert u["tools"] == ["run_command"] and u["body"] == "Run them."
    assert c.put("/api/units/x", json={"model": "gpt-4"}, headers=H).status_code == 400
    assert "test-runner" in c.get("/api/state", headers=H).json()["units"]
    assert c.delete("/api/units/test-runner", headers=H).json()["ok"]
    assert c.get("/api/jobs", headers=H).json() == []

    with c.websocket_connect(f"/ws?token={app.state.token}") as ws:
        sid = ws.receive_json()["session"]
        ws.send_json({"type": "directive", "text": "ping"})
        while not (lambda m: m["type"] == "event" and m["kind"] == "done")(ws.receive_json()):
            pass
    assert c.get("/api/sessions/search?q=tower", headers=H).json()[0]["id"] == sid
    r = c.post(f"/api/sessions/{sid}/fork", json={"turn": 0}, headers=H).json()
    assert r["directive"] == "ping" and r["title"].startswith("⟲")
    assert c.post(f"/api/sessions/{sid}/fork", json={"turn": 3}, headers=H).status_code == 400
