"""`pale` command line. The chat REPL is one more thin adapter over Agent."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import blackbox, config
from .agent import Agent
from .config import Mode
from .events import ApprovalDecision, ApprovalRequest, Event
from .memory import Memory, Skills
from .runtime import init_project, list_sessions, start_scheduler, transcript, unattended_run
from .scheduler import ScheduleStore
from .theme import BANNER, BOX, TAGLINE, boot_line, console, rule

app = typer.Typer(add_completion=False, no_args_is_help=False, help="PALE // operator support harness.")
memory_app = typer.Typer(help="Long-term memory.")
skills_app = typer.Typer(help="Skills and the quarantine.")
schedule_app = typer.Typer(help="Scheduled directives.")
audit_app = typer.Typer(help="The black box.")
app.add_typer(memory_app, name="memory")
app.add_typer(skills_app, name="skills")
app.add_typer(schedule_app, name="schedule")
app.add_typer(audit_app, name="audit")


def _settings(**over) -> config.Settings:
    s = config.load(**{k: v for k, v in over.items() if v is not None})
    s.ensure_dirs()
    return s


# --------------------------------------------------------------------- rendering

class Renderer:
    """Turns agent events into terminal output."""

    def __init__(self):
        self.streaming = None  # "text" | "thinking" | None
        self.lock = threading.Lock()
        self.last_call = None

    def _end_stream(self):
        if self.streaming:
            console.print()
            self.streaming = None

    def __call__(self, ev: Event) -> None:
        with self.lock:
            k, d = ev.kind, ev.data
            if k in ("text", "thinking"):
                if self.streaming != k:
                    self._end_stream()
                    console.print("[unit]◆ UNIT[/] " if k == "text" else "[think]  ∴ [/]", end="")
                    self.streaming = k
                console.print(Text(d["delta"], style="bone" if k == "text" else "think"), end="")
                return
            if k == "warning" and d.get("message", "").startswith("malformed"):
                return
            self._end_stream()
            if k == "tool_call":
                verdict = {"allow": "", "ask": " [ochre]· awaiting operator[/]", "deny": ""}[d["verdict"]]
                console.print(f"  [dust]■[/] [tool]{d['tool']}[/] [ash]{escape(d['arg'][:120])}[/] "
                              f"[risk.{d['risk']}]{d['risk']}[/]{verdict}")
                self.last_call = d["id"]
            elif k == "tool_result":
                if d["tool"] == "todo" and d["ok"]:
                    return  # the todo event already drew the plan
                if d["id"] != self.last_call:
                    console.print(f"    [dust]↳ {d['tool']}[/]")
                self.last_call = None
                first = d["output"].strip().splitlines()[:3]
                style = "dust" if d["ok"] else "rust"
                for line in first:
                    console.print(f"    [{style}]│ {escape(line[:150])}[/]")
                more = len(d["output"].strip().splitlines()) - 3
                if more > 0:
                    console.print(f"    [dust]│ … {more} more lines[/]")
            elif k == "denied":
                console.print(f"    [rust]✕ denied:[/] [ash]{escape(d['reason'])}[/]")
            elif k == "sub_tool_call":
                console.print(f"    [dust]· {d.get('unit', 'support')} unit → {d['tool']} {escape(d['arg'][:80])}[/]")
            elif k == "sub_denied":
                console.print(f"    [rust]✕ {d.get('unit', 'support')} unit denied:[/] [ash]{escape(d['reason'])}[/]")
            elif k == "mode":
                console.print(f"  [ochre]mode → {d['mode'].upper()}[/]")
            elif k == "job":
                state = "running" if d["running"] else (d["stopped"] or f"exit {d['code']}")
                console.print(f"  [dust]▶ job {d['id']} · {state} · {escape(d['command'][:80])}[/]")
            elif k == "review":
                if d["clean"]:
                    console.print("  [moss]■ review unit: no findings[/]")
                else:
                    console.print(Panel(Text(d["text"][:3000], style="bone"), title="[rust]REVIEW UNIT FINDINGS[/]",
                                        subtitle="[ash]the unit sees these with your next directive[/]", box=BOX,
                                        border_style="rust", title_align="left"))
            elif k == "todo":
                mark = {"pending": "[dust]□[/]", "active": "[ochre]▣[/]", "done": "[moss]■[/]"}
                for i in d["items"]:
                    console.print(f"    {mark[i['status']]} [ash]{escape(i['text'])}[/]")
            elif k == "warning":
                console.print(f"  [ochre]⚠ {escape(d['message'])}[/]")
            elif k == "error":
                console.print(f"  [rust]✕ {escape(d['message'])}[/]")
            elif k == "status" and d.get("message") not in (None, "thinking"):
                console.print(f"  [dust]» {escape(d['message'])}[/]")
            elif k == "done":
                console.print(f"[dust]  ── {d['steps']} step(s) · ${d['usd']:.4f} this session[/]")


def cli_approver(req: ApprovalRequest) -> ApprovalDecision:
    body = []
    if req.tool == "submit_plan":
        console.print(Panel(Text(req.preview[:8000], style="bone"), title="[ochre]PLAN REVIEW[/] [ash]nothing changes until you approve[/]",
                            box=BOX, border_style="ochre", title_align="left"))
        ans = console.input("[op]  approve plan?[/] [ash]\\[y]es  \\[n <what to change>][/] › ").strip()
        if ans.lower() in ("y", "yes"):
            return ApprovalDecision(True)
        note = ans.split(" ", 1)[1] if ans.lower().startswith(("n ", "no ")) else ""
        return ApprovalDecision(False, note=note)
    if req.preview:
        lexer = "diff" if req.preview.startswith(("---", "@@")) or "\n+" in req.preview else "bash"
        body.append(Syntax(req.preview[:6000], lexer, theme="ansi_dark", word_wrap=True, background_color="default"))
    reasons = " · ".join(r for r in req.reasons if r)
    title = f"[risk.{req.risk}] {req.risk.upper()} [/] [bone]{escape(req.summary)}[/]"
    console.print(Panel(body[0] if body else Text(""), title=title, subtitle=f"[ash]{escape(reasons)}[/]",
                        box=BOX, border_style="rust" if req.risk == "high" else "ash", title_align="left"))
    while True:
        ans = console.input("[op]  authorize?[/] [ash]\\[y]es  \\[n]o  \\[a]lways this session  \\[n <reason>][/] › ").strip()
        low = ans.lower()
        if low in ("y", "yes"):
            return ApprovalDecision(True)
        if low in ("a", "always"):
            if req.risk == "high":
                console.print("  [ochre]high-risk actions can't be pre-approved; approving once.[/]")
                return ApprovalDecision(True)
            return ApprovalDecision(True, always=True)
        if low in ("n", "no", ""):
            return ApprovalDecision(False)
        if low.startswith(("n ", "no ")):
            return ApprovalDecision(False, note=ans.split(" ", 1)[1])


def cli_ask(question: str) -> str:
    console.print(Panel(Text(question, style="bone"), title="[op]UNIT REQUESTS INPUT[/]", box=BOX, border_style="ochre", title_align="left"))
    return console.input("[op]  operator ›[/] ")


def banner(settings: config.Settings, agent: Agent) -> None:
    console.print(Text(BANNER, style="bone"))
    console.print(f" [ash]{TAGLINE}[/]")
    console.print(f" [dust]{boot_line()}[/]\n")
    t = Table.grid(padding=(0, 2))
    t.add_row("[dust]UNIT[/]", f"[bone]{settings.unit_name}[/]", "[dust]MODEL[/]", f"[bone]{settings.model}[/]")
    t.add_row("[dust]MODE[/]", f"[ochre]{agent.mode.value.upper()}[/]", "[dust]DRY RUN[/]",
              "[ochre]ON[/]" if settings.dry_run else "[ash]off[/]")
    t.add_row("[dust]SANDBOX[/]", f"[ash]{settings.sandbox}[/]", "[dust]SESSION[/]", f"[ash]{agent.session_id}[/]")
    t.add_row("[dust]BUDGET[/]", f"[ash]${settings.budget_session_usd:.2f}/session · ${settings.budget_daily_usd:.2f}/day[/]",
              "[dust]TODAY[/]", f"[ash]${agent.budget.ledger.today():.4f}[/]")
    console.print(t)
    console.print("\n [dust]/help for commands · ctrl-c interrupts the unit · /quit to leave[/]")
    rule()


HELP = """\
/mode <observe|plan|ask|edits|autonomous>   change approval mode
/dry [on|off]                          toggle dry run
/undo [id]                             roll back the last directive's file changes
/checkpoints                           list rollback points
/rewind <n>                            new session from before directive n (see /sessions)
/jobs [stop <id>]                      background jobs
/units                                 specialist units in units/
/todo                                  show the unit's plan
/cost                                  token and dollar meter
/memory  /skills                       list memory and skills
/sessions  /resume <id>  /new          session management
/audit                                 recent black-box entries
/clear                                 clear the screen
/quit                                  leave"""


def run_with_interrupt(agent: Agent, directive: str) -> str | None:
    result: dict = {}

    def target():
        try:
            result["out"] = agent.run(directive)
        except Exception as e:
            result["err"] = e

    th = threading.Thread(target=target, daemon=True)
    th.start()
    while th.is_alive():
        try:
            th.join(0.2)
        except KeyboardInterrupt:
            console.print("\n  [ochre]⚠ interrupt sent. The unit will stop after the current action.[/]")
            agent.interrupt()
    if "err" in result:
        console.print(f"  [rust]✕ {escape(str(result['err']))}[/]")
        return None
    return result.get("out")


def handle_slash(cmd: str, agent: Agent, settings: config.Settings, renderer: Renderer) -> Agent | None:
    """Returns a replacement agent for /new and /resume, else None."""
    parts = cmd.split()
    name, args = parts[0].lower(), parts[1:]
    if name == "/help":
        console.print(Panel(HELP, box=BOX, border_style="dust", title="[ash]COMMANDS[/]", title_align="left"))
    elif name == "/mode":
        if not args or args[0] not in Mode.__members__:
            console.print(f"  [ash]mode is {agent.mode.value}. choose: {', '.join(Mode.__members__)}[/]")
        else:
            agent.set_mode(args[0])
            console.print(f"  [ochre]mode → {args[0].upper()}[/]")
    elif name == "/dry":
        settings.dry_run = (args[0] == "on") if args else not settings.dry_run
        console.print(f"  [ochre]dry run {'ON' if settings.dry_run else 'off'}[/]")
    elif name == "/undo":
        r = agent.checkpoints.undo(args[0] if args else None)
        if r["id"]:
            console.print(f"  [moss]restored {len(r['restored'])} file(s) from '{escape(r['label'][:60])}':[/] [ash]{', '.join(r['restored'])}[/]")
        else:
            console.print("  [ash]nothing to undo[/]")
    elif name == "/checkpoints":
        for c in agent.checkpoints.list()[:15]:
            console.print(f"  [ash]{c['id']}[/]  [bone]{escape(c['label'][:50])}[/]  [dust]{len(c['files'])} file(s)[/]")
    elif name == "/todo":
        renderer(Event("todo", {"items": agent.todos})) if agent.todos else console.print("  [ash]no plan[/]")
    elif name == "/cost":
        m = agent.budget.meter
        console.print(f"  [ash]session ${m.usd:.4f} · in {m.input_tokens:,} · out {m.output_tokens:,} · cache-read {m.cache_read:,} · today ${agent.budget.ledger.today():.4f}[/]")
    elif name == "/memory":
        idx = agent.memory.index()
        console.print(f"[ash]{escape(idx)}[/]" if idx else "  [ash]memory empty[/]")
    elif name == "/skills":
        _print_skills(agent.skills)
    elif name == "/sessions":
        _print_sessions(settings)
    elif name == "/resume" and args:
        new = make_cli_agent(settings, renderer, args[0])
        console.print(f"  [moss]resumed {args[0]} ({len(new.messages)} messages)[/]")
        for row in transcript(new.messages)[-6:]:
            console.print(f"  [dust]{row['who']}:[/] [ash]{escape(row['text'][:160])}[/]")
        return new
    elif name == "/new":
        console.print("  [moss]new session[/]")
        return make_cli_agent(settings, renderer)
    elif name == "/audit":
        for e in agent.blackbox.tail(12, agent.session_id):
            console.print(f"  [dust]{datetime.fromtimestamp(e['ts']):%H:%M:%S}[/] [ash]{e['kind']:<12}[/] [dust]{escape(json.dumps(e['data'])[:110])}[/]")
    elif name == "/jobs":
        from .jobs import jobs_for

        jobs = jobs_for(settings)
        if args[:1] == ["stop"] and len(args) > 1:
            try:
                jobs.stop(args[1])
                console.print(f"  [moss]stopped {args[1]}[/]")
            except KeyError:
                console.print(f"  [ash]no job {args[1]}[/]")
        for j in jobs.jobs.values():
            i = j.info()
            console.print(f"  [ash]{i['id']}[/] [bone]{'running' if i['running'] else i['stopped'] or 'exit ' + str(i['code'])}[/]"
                          f" [dust]{i['seconds']}s · {escape(i['command'][:80])}[/]")
        if not jobs.jobs:
            console.print("  [ash]no background jobs[/]")
    elif name == "/units":
        _print_units(settings)
    elif name == "/rewind":
        from .runtime import directive_turns, fork_session

        agent.save()
        turns = directive_turns(agent.messages)
        if not args or not args[0].isdigit():
            for n, i in enumerate(turns):
                text = next((b["text"] for b in agent.messages[i]["content"] if isinstance(b, dict) and b.get("type") == "text"), "")
                console.print(f"  [ash]{n}[/] [bone]{escape(text[:100])}[/]")
            console.print("  [dust]/rewind <n> starts a new session from just before directive n. Files are not changed.[/]")
        else:
            try:
                r = fork_session(settings, agent.session_id, int(args[0]))
            except IndexError as e:
                console.print(f"  [ash]{e}[/]")
                return None
            console.print(f"  [moss]rewound into new session {r['id']}. The directive was:[/] [ash]{escape(r['directive'][:200])}[/]")
            return make_cli_agent(settings, renderer, r["id"])
    elif name == "/clear":
        console.clear()
    else:
        console.print("  [ash]unknown command. /help[/]")
    return None


def _print_units(settings) -> None:
    from .units import load_units

    units = load_units(settings.units_dir)
    if not units:
        console.print(f"  [ash]no specialist units. Add Markdown files to {settings.units_dir}[/]")
    for u in units.values():
        tools = ", ".join(u.tools) if u.tools else "read-only set"
        console.print(f"  [bone]{u.name}[/] [ash]{escape(u.description)}[/] [dust]· {u.model or 'sub model'} · {tools}[/]")


def make_cli_agent(settings, renderer, session_id=None) -> Agent:
    return Agent(settings, approver=cli_approver, sink=renderer, ask_fn=cli_ask, session_id=session_id, surface="cli")


def _require_key() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        console.print("[ochre]⚠ ANTHROPIC_API_KEY is not set. Set it (or run `ant auth login`) before sending directives.[/]")


# --------------------------------------------------------------------- commands

@app.command()
def init(path: Path = typer.Argument(Path("."), help="Project folder")):
    """Create pale.toml, sandbox/ and bunker/ in a folder."""
    made = init_project(path.resolve())
    for m in made:
        console.print(f"  [moss]+[/] [ash]{m}[/]")
    console.print(f"[bone]Sector initialized at {path.resolve()}[/]. Put files for the unit in [ochre]sandbox/[/], then run [ochre]pale chat[/].")


@app.command()
def chat(
    resume: Optional[str] = typer.Option(None, help="Session id to resume"),
    mode: Optional[Mode] = typer.Option(None, help="Approval mode"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mutating tools only describe what they'd do"),
    model: Optional[str] = typer.Option(None, help="Model id"),
):
    """Interactive session with the unit."""
    settings = _settings(mode=mode, model=model, dry_run=dry_run or None)
    renderer = Renderer()
    agent = make_cli_agent(settings, renderer, resume)
    banner(settings, agent)
    _require_key()
    while True:
        try:
            text = console.input("[op]OPERATOR ›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dust]Link severed. The city waits.[/]")
            return
        if not text:
            continue
        if text in ("/quit", "/exit", "quit", "exit"):
            console.print("[dust]Link severed. The city waits.[/]")
            return
        if text.startswith("/"):
            new = handle_slash(text, agent, settings, renderer)
            if new:
                agent = new
            continue
        run_with_interrupt(agent, text)
        rule()


@app.command()
def run(
    directive: str = typer.Argument(..., help="What the unit should do"),
    mode: Optional[Mode] = typer.Option(None),
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_steps: Optional[int] = typer.Option(None),
    model: Optional[str] = typer.Option(None),
    no_input: bool = typer.Option(False, "--no-input", help="Never prompt: approval requests are denied"),
):
    """One directive, then exit. Without a TTY (or with --yes), approval requests are denied."""
    settings = _settings(mode=mode, dry_run=dry_run or None, max_steps=max_steps, model=model)
    interactive = sys.stdin.isatty() and not no_input
    agent = Agent(settings, approver=cli_approver if interactive else (lambda r: ApprovalDecision(False, "non-interactive run")),
                  sink=Renderer(), ask_fn=cli_ask if interactive else None, surface="cli-run", unattended=not interactive)
    _require_key()
    out = run_with_interrupt(agent, directive)
    raise typer.Exit(0 if out is not None and not out.startswith("[halted]") else 1)


@app.command()
def serve(host: Optional[str] = None, port: Optional[int] = None, no_scheduler: bool = False):
    """Web terminal on localhost (plus the scheduler)."""
    try:
        import uvicorn
        from .web.server import create_app
    except ImportError:
        console.print("[rust]web extras missing:[/] pip install 'pale[web]'")
        raise typer.Exit(1)
    settings = _settings()
    h, p = host or settings.web_host, port or settings.web_port
    if h not in ("127.0.0.1", "localhost"):
        console.print("[rust]⚠ binding beyond localhost exposes a shell-capable agent to the network.[/]")
    console.print(Text(BANNER, style="bone"))
    console.print(f" [ash]{TAGLINE}[/]\n [bone]web terminal → http://{h}:{p}[/]  [dust]token in the URL printed below[/]")
    app_ = create_app(settings, scheduler=not no_scheduler)
    console.print(f" [ochre]http://{h}:{p}/?token={app_.state.token}[/]")
    uvicorn.run(app_, host=h, port=p, log_level="warning")


@app.command()
def desktop():
    """Open the desktop app window (needs: pip install 'pale[desktop]')."""
    from .desktop.app import main as desktop_main

    desktop_main()


@app.command()
def gateway(scheduler: bool = typer.Option(True, help="Also run scheduled directives")):
    """Telegram gateway: talk to the same unit, memory and skills from your phone."""
    from .gateway.telegram import TelegramGateway

    settings = _settings()
    gw = TelegramGateway(settings)
    if scheduler:
        sched = start_scheduler(settings)
        sched.notifiers.append(gw.notify_report)
    console.print(f"[bone]Telegram gateway online.[/] [dust]allowed users: {settings.telegram.allowed_user_ids}[/]")
    gw.run_forever()


@app.command()
def daemon():
    """Run only the scheduler, in the foreground."""
    settings = _settings()
    start_scheduler(settings)
    console.print("[bone]Scheduler running.[/] [dust]ctrl-c to stop[/]")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


@app.command()
def units():
    """List specialist units (units/*.md) the unit can dispatch."""
    _print_units(_settings())


@app.command()
def sessions():
    """List saved sessions."""
    _print_sessions(_settings())


def _print_sessions(settings):
    t = Table(box=BOX, border_style="dust", header_style="ash")
    for c in ("id", "title", "surface", "msgs", "cost", "updated"):
        t.add_column(c)
    for r in list_sessions(settings):
        t.add_row(r["id"], escape(r["title"][:40]), r["surface"], str(r["turns"]), f"${r['usd']:.4f}",
                  datetime.fromtimestamp(r["updated"]).strftime("%m-%d %H:%M"))
    console.print(t)


@app.command()
def undo(checkpoint: Optional[str] = typer.Argument(None)):
    """Roll back the most recent directive's file changes (or a given checkpoint)."""
    s = _settings()
    from .checkpoints import Checkpoints

    r = Checkpoints(s.bunker / "checkpoints", s.sandbox).undo(checkpoint)
    console.print(f"[moss]restored:[/] {', '.join(r['restored'])}" if r["id"] else "[ash]nothing to undo[/]")


@app.command()
def cost():
    """Spend per day."""
    s = _settings()
    try:
        data = json.loads((s.bunker / "ledger.json").read_text())
    except FileNotFoundError:
        data = {}
    for day, usd in sorted(data.items())[-14:]:
        bar = "█" * min(40, int(usd / max(s.budget_daily_usd, 0.01) * 40))
        console.print(f"  [ash]{day}[/]  [bone]${usd:8.4f}[/]  [dust]{bar}[/]")
    console.print(f"  [dust]daily cap ${s.budget_daily_usd:.2f}[/]")


@app.command()
def doctor():
    """Check the installation."""
    s = _settings()
    ok = lambda b: "[moss]■ ok[/]" if b else "[rust]✕ no[/]"  # noqa: E731
    rows = [
        ("pale.toml found", (s.root / "pale.toml").exists()),
        ("API credentials in env", bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))),
        ("sandbox exists", s.sandbox.is_dir()),
        ("black box intact", blackbox.verify(s.bunker / "blackbox.jsonl")[0]),
        ("docker available (optional)", shutil.which("docker") is not None),
        ("telegram token set (optional)", bool(os.environ.get(s.telegram.token_env))),
    ]
    for label, good in rows:
        console.print(f"  {ok(good)}  [ash]{label}[/]")
    console.print(f"  [dust]mode {s.mode.value} · model {s.model} · shell {s.shell_backend}[/]")


# ---- memory

@memory_app.command("list")
def memory_list():
    for n in Memory(_settings().bunker / "memory").all():
        console.print(f"  [bone]{n.name}[/]  [ash]{escape(n.description)}[/]")


@memory_app.command("show")
def memory_show(name: str):
    n = Memory(_settings().bunker / "memory").get(name)
    console.print(Panel(escape(n.body), title=n.name, box=BOX, border_style="dust") if n else "[ash]not found[/]")


@memory_app.command("forget")
def memory_forget(name: str):
    console.print("[moss]forgotten[/]" if Memory(_settings().bunker / "memory").delete(name) else "[ash]not found[/]")


# ---- skills

def _print_skills(sk: Skills):
    for n in sk.all():
        console.print(f"  [moss]■[/] [bone]{n.name}[/]  [ash]{escape(n.description)}[/]")
    for n in sk.quarantine.all():
        console.print(f"  [ochre]▣[/] [bone]{n.name}[/]  [ash]{escape(n.description)}[/]  [ochre](quarantined)[/]")


@skills_app.command("list")
def skills_list():
    _print_skills(Skills(_settings().bunker / "skills"))


@skills_app.command("show")
def skills_show(name: str):
    sk = Skills(_settings().bunker / "skills")
    n = sk.get(name) or sk.quarantine.get(name)
    console.print(Panel(escape(n.body), title=f"{n.name} · {escape(n.description)}", box=BOX, border_style="dust") if n else "[ash]not found[/]")


@skills_app.command("approve")
def skills_approve(name: str):
    """Release a quarantined skill so the unit can use it. Read it first: pale skills show NAME."""
    try:
        Skills(_settings().bunker / "skills").approve(name)
        console.print(f"[moss]skill {name} approved[/]")
    except KeyError:
        console.print("[ash]no quarantined skill by that name[/]")


@skills_app.command("reject")
def skills_reject(name: str):
    console.print("[moss]rejected[/]" if Skills(_settings().bunker / "skills").reject(name) else "[ash]not found[/]")


# ---- schedule

@schedule_app.command("add")
def schedule_add(spec: str = typer.Argument(..., help="'every 30m' | 'daily 09:00' | 'weekdays 08:30' | 'once 2026-11-01 09:00'"),
                 directive: str = typer.Argument(...), name: str = ""):
    t = ScheduleStore(_settings().bunker / "schedule.json").add(spec, directive, name)
    console.print(f"[moss]scheduled {t['id']}[/] [ash]next {t['next']}[/]")


@schedule_app.command("list")
def schedule_list():
    t = Table(box=BOX, border_style="dust", header_style="ash")
    for c in ("id", "name", "spec", "next", "last"):
        t.add_column(c)
    for r in ScheduleStore(_settings().bunker / "schedule.json").load():
        t.add_row(r["id"], escape(r["name"]), r["spec"], str(r["next"]), str(r["last"]))
    console.print(t)


@schedule_app.command("remove")
def schedule_remove(task_id: str):
    console.print("[moss]removed[/]" if ScheduleStore(_settings().bunker / "schedule.json").remove(task_id) else "[ash]not found[/]")


@schedule_app.command("run-now")
def schedule_run_now(task_id: str):
    s = _settings()
    task = next((t for t in ScheduleStore(s.bunker / "schedule.json").load() if t["id"] == task_id), None)
    if not task:
        raise typer.Exit(1)
    console.print(escape(unattended_run(s, task)))


# ---- audit

@audit_app.command("verify")
def audit_verify():
    """Check the black box's hash chain."""
    ok, n, msg = blackbox.verify(_settings().bunker / "blackbox.jsonl")
    console.print(f"[{'moss' if ok else 'rust'}]{'■' if ok else '✕'} {msg}[/] [dust]({n} entries)[/]")
    raise typer.Exit(0 if ok else 1)


@audit_app.command("tail")
def audit_tail(n: int = 30, session: Optional[str] = None):
    for e in blackbox.BlackBox(_settings().bunker / "blackbox.jsonl").tail(n, session):
        console.print(f"[dust]{datetime.fromtimestamp(e['ts']):%m-%d %H:%M:%S}[/] [ash]{e['session'][-6:]}[/] "
                      f"[bone]{e['kind']:<14}[/] [dust]{escape(json.dumps(e['data'])[:140])}[/]")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        ctx.invoke(chat, resume=None, mode=None, dry_run=False, model=None)


if __name__ == "__main__":
    app()
