"""Shared wiring for every surface: building agents, unattended runs, session listing."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .agent import Agent
from .config import Mode, Settings
from .events import deny_all
from .scheduler import Scheduler, ScheduleStore


def unattended_run(settings: Settings, task: dict) -> str:
    """Run a scheduled directive with nobody watching. Approval-gated actions are refused; never
    more permissive than `edits` mode, even if the operator's default is autonomous."""
    if settings.mode == Mode.plan:
        mode = Mode.observe  # nobody is there to approve a plan
    else:
        mode = settings.mode if settings.mode in (Mode.observe, Mode.ask, Mode.edits) else Mode.edits
    s = settings.model_copy(update={"mode": mode})
    agent = Agent(s, approver=deny_all, surface="scheduler", unattended=True)
    agent.title = f"[{'watch' if task.get('watch') else 'scheduled'}] {task['name']}"
    directive = task["directive"]
    if task.get("changed"):
        directive += "\n\nTriggered by changes to: " + ", ".join(task["changed"])
    report = agent.run(directive)
    out = settings.bunker / "reports" / f"{time.strftime('%Y%m%d-%H%M')}-{task['id']}.md"
    out.write_text(
        f"# {task['name']}\n\n- directive: {task['directive']}\n- session: {agent.session_id}\n"
        f"- cost: ${agent.budget.meter.usd:.4f}\n\n{report}\n",
        encoding="utf-8",
    )
    return report


def start_scheduler(settings: Settings) -> Scheduler:
    sched = Scheduler(ScheduleStore(settings.bunker / "schedule.json"), lambda t: unattended_run(settings, t),
                      watch_root=settings.sandbox)
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


def _blocks(m: dict) -> list[dict]:
    return m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]


def directive_turns(messages: list[dict]) -> list[int]:
    """Indexes of the user messages that carry a directive (text), not only tool results."""
    return [i for i, m in enumerate(messages)
            if m["role"] == "user" and any(b.get("type") == "text" for b in _blocks(m))]


def transcript(messages: list[dict]) -> list[dict]:
    """Flatten stored API messages into display rows for UIs. Operator rows carry `turn`, the
    directive number a rewind forks from."""
    rows = []
    turns = {i: n for n, i in enumerate(directive_turns(messages))}
    for idx, m in enumerate(messages):
        for b in _blocks(m):
            t = b.get("type")
            if t == "text" and b.get("text"):
                row = {"who": m["role"], "kind": "text", "text": b["text"]}
                if idx in turns:
                    row["turn"] = turns[idx]
                rows.append(row)
            elif t == "tool_use":
                rows.append({"who": "unit", "kind": "tool_call", "tool": b["name"], "text": json.dumps(b.get("input"))[:300]})
            elif t == "tool_result":
                c = b.get("content")
                text = c if isinstance(c, str) else json.dumps(c)
                rows.append({"who": "harness", "kind": "tool_result", "ok": not b.get("is_error"), "text": text[:2000]})
    return rows


def fork_session(settings: Settings, sid: str, turn: int) -> dict:
    """Rewind: a new session holding the conversation from before directive number `turn`.
    The original is kept. Files are not touched (use undo for that)."""
    src = settings.bunker / "sessions" / f"{sid}.json"
    d = json.loads(src.read_text())
    msgs = d.get("messages", [])
    turns = directive_turns(msgs)
    if not 0 <= turn < len(turns):
        raise IndexError(f"session has {len(turns)} directives")
    cut = turns[turn]
    head = msgs[:cut]
    blocks = _blocks(msgs[cut])
    results = [b for b in blocks if b.get("type") == "tool_result"]
    if results:  # the cut message also closed the previous turn's tool calls; keep those
        head.append({"role": "user", "content": results})
    directive = "\n".join(b["text"] for b in blocks if b.get("type") == "text" and not b["text"].startswith(
        ("Attached files (in the sandbox):", "[review unit findings")))
    new_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    title = d.get("title", "") or "untitled"
    out = {"id": new_id, "title": ("⟲ " + title.removeprefix("⟲ "))[:90], "surface": d.get("surface", ""),
           "updated": time.time(), "messages": head, "todos": [], "usd": 0.0, "forked_from": sid, "fork_turn": turn}
    (settings.bunker / "sessions" / f"{new_id}.json").write_text(json.dumps(out, ensure_ascii=False))
    return {"id": new_id, "title": out["title"], "directive": directive}


def search_sessions(settings: Settings, query: str, limit: int = 40) -> list[dict]:
    """Case-insensitive full-text search over saved sessions (text, tool calls and results)."""
    q = query.strip().lower()
    if not q:
        return []
    hits = []
    folder = settings.bunker / "sessions"
    for p in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        rows = transcript(d.get("messages", []))
        rows.sort(key=lambda r: r["kind"] != "text")  # prefer a hit in what was said over tool JSON
        rows.append({"who": "", "kind": "title", "text": d.get("title", "")})
        for row in rows:
            text = row.get("text", "")
            at = text.lower().find(q)
            if at >= 0:
                lo = max(0, at - 60)
                hits.append({"id": d["id"], "title": d.get("title", ""), "updated": d.get("updated", 0),
                             "who": row.get("who", ""), "snippet": ("…" if lo else "") + text[lo:at + len(q) + 100]})
                break
        if len(hits) >= limit:
            break
    return hits


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
    units = root / "units"
    if not units.exists():
        from .units import EXAMPLE

        units.mkdir()
        (units / "reviewer.md").write_text(EXAMPLE, encoding="utf-8")
        made.append("units/reviewer.md")
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
