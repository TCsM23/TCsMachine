"""The agent loop, driven by a scripted fake model: no API key, no cost, real side effects in tmp dirs."""

from pale.config import Mode
from pale.events import ApprovalDecision
from pale.testing import FakeModel, call, say, say_and_call
from pale.model import Reply

from conftest import last_results


def test_history_is_sent_every_turn(make_agent):
    model = FakeModel([say("Noted, TCsM."), say("Your name is TCsM.")])
    agent = make_agent(model)
    agent.run("my name is TCsM")
    agent.run("what's my name?")
    sent = model.requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert "TCsM" in str(sent[0])


def test_read_file_cycle(make_agent, settings):
    (settings.sandbox / "notes.txt").write_text("alpha\nbeta\n")
    model = FakeModel([call("read_file", {"path": "notes.txt"}), say("It says alpha and beta.")])
    out = make_agent(model).run("what's in notes.txt?")
    assert out == "It says alpha and beta."
    (res,) = last_results(model)
    assert "alpha" in res["content"] and not res.get("is_error")


def test_parallel_calls_return_in_order_in_one_message(make_agent, settings):
    for n in "abc":
        (settings.sandbox / f"{n}.txt").write_text(n * 3)
    model = FakeModel([
        say_and_call("", *[("read_file", {"path": f"{n}.txt"}) for n in "abc"]),
        say("ok"),
    ])
    make_agent(model).run("read all")
    results = last_results(model)
    assert [r["content"].split("\t")[1] for r in results] == ["aaa", "bbb", "ccc"]


def test_sandbox_escape_is_denied(make_agent, settings, tmp_path):
    (tmp_path / "secret.txt").write_text("outside")
    (settings.sandbox / "link").symlink_to(tmp_path / "secret.txt")
    model = FakeModel([
        say_and_call("", ("read_file", {"path": "../secret.txt"}), ("read_file", {"path": "link"})),
        say("blocked"),
    ])
    make_agent(model).run("read outside")
    for r in last_results(model):
        assert r["is_error"] and "escapes the sandbox" in r["content"]


def test_absolute_paths_map_into_the_sandbox(make_agent, settings):
    (settings.sandbox / "etc").mkdir()
    (settings.sandbox / "etc" / "passwd").write_text("sandboxed copy")
    model = FakeModel([call("read_file", {"path": "/etc/passwd"}), say("ok")])
    make_agent(model).run("read /etc/passwd")
    assert "sandboxed copy" in last_results(model)[0]["content"]


def test_ask_mode_declined_write_changes_nothing(make_agent, settings):
    settings.mode = Mode.ask
    seen = []

    def approver(req):
        seen.append(req)
        return ApprovalDecision(False, "not today")

    model = FakeModel([call("write_file", {"path": "x.txt", "content": "hi"}), say("understood")])
    make_agent(model, approver=approver).run("write x")
    assert not (settings.sandbox / "x.txt").exists()
    assert seen and "+hi" in seen[0].preview
    (res,) = last_results(model)
    assert res["is_error"] and "not today" in res["content"]


def test_always_approval_is_remembered_for_the_session(make_agent, settings):
    settings.mode = Mode.ask
    asked = []
    model = FakeModel([
        call("run_command", {"command": "python -c 'print(1)'"}),
        call("run_command", {"command": "python -c 'print(2)'"}),
        say("done"),
    ])
    agent = make_agent(model, approver=lambda r: asked.append(r) or ApprovalDecision(True, always=True))
    agent.run("run twice")
    assert len(asked) == 1
    assert "2" in last_results(model)[0]["content"]


def test_observe_mode_refuses_mutations(make_agent, settings):
    settings.mode = Mode.observe
    model = FakeModel([call("write_file", {"path": "x.txt", "content": "hi"}), say("ok")])
    make_agent(model).run("write")
    assert not (settings.sandbox / "x.txt").exists()
    assert "observe mode" in last_results(model)[0]["content"]


def test_dry_run_simulates(make_agent, settings):
    settings.dry_run = True
    model = FakeModel([
        say_and_call("", ("write_file", {"path": "x.txt", "content": "hi"}), ("run_command", {"command": "touch y"})),
        say("ok"),
    ])
    make_agent(model).run("go")
    assert not (settings.sandbox / "x.txt").exists() and not (settings.sandbox / "y").exists()
    assert all("[dry run]" in r["content"] for r in last_results(model))


def test_step_limit_halts(make_agent, settings):
    settings.max_steps = 3
    (settings.sandbox / "a").write_text("a")
    model = FakeModel([call("read_file", {"path": "a"}) for _ in range(10)])
    out = make_agent(model).run("loop forever")
    assert out.startswith("[halted]") and len(model.requests) == 3


def test_next_directive_after_halt_merges_into_pending_user_turn(make_agent, settings):
    settings.max_steps = 1
    (settings.sandbox / "a").write_text("a")
    model = FakeModel([call("read_file", {"path": "a"}), say("resumed")])
    agent = make_agent(model)
    agent.run("start")
    agent.run("continue")
    roles = [m["role"] for m in model.requests[-1]["messages"]]
    assert roles == ["user", "assistant", "user"]  # tool_result + new text share one user turn


def test_forbidden_command_denied_even_when_autonomous(make_agent, settings):
    model = FakeModel([call("run_command", {"command": "rm -rf /"}), say("ok")])
    make_agent(model).run("wipe")
    assert "forbidden" in last_results(model)[0]["content"]


def test_high_risk_asks_even_when_autonomous(make_agent, settings):
    asked = []
    model = FakeModel([call("run_command", {"command": "git push origin main"}), say("ok")])
    make_agent(model, approver=lambda r: asked.append(r) or ApprovalDecision(False)).run("push")
    assert asked and asked[0].risk == "high"


def test_read_before_overwrite(make_agent, settings):
    (settings.sandbox / "f.txt").write_text("original")
    model = FakeModel([
        call("write_file", {"path": "f.txt", "content": "clobber"}),
        call("read_file", {"path": "f.txt"}),
        call("write_file", {"path": "f.txt", "content": "new"}),
        say("ok"),
    ])
    make_agent(model).run("overwrite")
    assert "read f.txt with read_file" in model.requests[1]["messages"][-1]["content"][0]["content"]
    assert (settings.sandbox / "f.txt").read_text() == "new"


def test_edit_file_needs_unique_match(make_agent, settings):
    (settings.sandbox / "f.py").write_text("x = 1\nx = 1\n")
    model = FakeModel([
        call("read_file", {"path": "f.py"}),
        call("edit_file", {"path": "f.py", "old_string": "x = 1", "new_string": "x = 2"}),
        call("edit_file", {"path": "f.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True}),
        say("ok"),
    ])
    make_agent(model).run("edit")
    assert "matches 2 times" in model.requests[2]["messages"][-1]["content"][0]["content"]
    assert (settings.sandbox / "f.py").read_text() == "x = 2\nx = 2\n"


def test_undo_rolls_back_a_directive(make_agent, settings):
    f = settings.sandbox / "f.txt"
    f.write_text("v1")
    model = FakeModel([
        call("read_file", {"path": "f.txt"}),
        say_and_call("", ("write_file", {"path": "f.txt", "content": "v2"}), ("write_file", {"path": "new.txt", "content": "n"})),
        say("ok"),
    ])
    agent = make_agent(model)
    agent.run("change things")
    assert f.read_text() == "v2"
    undone = agent.checkpoints.undo()
    assert f.read_text() == "v1" and not (settings.sandbox / "new.txt").exists()
    assert set(undone["restored"]) == {"f.txt", "new.txt"}


def test_invalid_tool_input_is_reported_not_crashed(make_agent):
    model = FakeModel([call("read_file", {"wrong": 1}), say("ok")])
    make_agent(model).run("bad input")
    assert "invalid input" in last_results(model)[0]["content"]


def test_max_tokens_mid_tool_call_is_repaired(make_agent):
    model = FakeModel([
        Reply([{"type": "tool_use", "id": "toolu_x", "name": "write_file", "input": {"path": "big"}}], "max_tokens", say("").usage, "fake"),
        say("ok"),
    ])
    make_agent(model).run("big write")
    (res,) = last_results(model)
    assert res["tool_use_id"] == "toolu_x" and "max_tokens" in res["content"]


def test_interrupt_closes_pending_calls(make_agent, settings):
    agent = None

    def interrupting_approver(req):
        agent.interrupt()
        return ApprovalDecision(True)

    settings.mode = Mode.ask
    model = FakeModel([
        say_and_call("", ("run_command", {"command": "echo 1"}), ("run_command", {"command": "echo 2"})),
        say("never"),
    ])
    agent = make_agent(model, approver=interrupting_approver)
    out = agent.run("two commands")
    assert "interrupted" in out
    last = agent.messages[-1]
    assert last["role"] == "user" and len(last["content"]) == 2


def test_subagent_cannot_write(make_agent, settings):
    model = FakeModel([
        call("dispatch_unit", {"task": "look around"}),
        call("write_file", {"path": "x.txt", "content": "sub"}),  # the subagent tries
        say("found nothing"),  # subagent report
        say("done"),  # parent
    ])
    make_agent(model).run("investigate")
    assert not (settings.sandbox / "x.txt").exists()
    assert "support unit report" in last_results(model)[0]["content"]
