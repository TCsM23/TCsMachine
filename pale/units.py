"""Specialist units: operator-written sub-agent definitions, after Claude Code's custom subagents.

    <root>/units/reviewer.md
    ---
    name: reviewer
    description: reviews a diff for bugs; use after any non-trivial change
    model: claude-haiku-5-5        (optional; default sub_model)
    tools: read_file, search, git_diff   (optional; default the read-only support set)
    max_steps: 12                  (optional)
    ---
    You review code changes. Report only real defects, with file:line ...

The folder sits outside the sandbox, so the unit can't write its own specialists. A unit that
lists mutating tools gets them only when the parent isn't in observe or plan mode, and every
call still goes through the parent's approvals, rules and black box.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .memory import parse, slugify

NEVER = {"dispatch_unit", "ask_operator", "submit_plan", "schedule_directive", "propose_skill"}  # no recursion, no escalation


@dataclass
class UnitDef:
    name: str
    description: str
    body: str
    model: str = ""
    tools: list[str] = field(default_factory=list)
    max_steps: int = 15
    path: Path | None = None


def load_units(folder: Path) -> dict[str, UnitDef]:
    out: dict[str, UnitDef] = {}
    if not folder.is_dir():
        return out
    for p in sorted(folder.glob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        try:
            n = parse(p)
            name = slugify(n.name)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        tools = [t for t in re.split(r"[,\s\[\]\"']+", n.meta.get("tools", "")) if t and t not in NEVER]
        try:
            steps = max(1, min(int(n.meta.get("max_steps", 15)), 60))
        except ValueError:
            steps = 15
        model = n.meta.get("model", "").strip()
        if model and not re.fullmatch(r"claude-[\w.-]+", model):
            model = ""
        out[name] = UnitDef(name, n.description, n.body, model, tools, steps, p)
    return out


EXAMPLE = """\
---
name: reviewer
description: reviews a diff for real bugs; dispatch it after a non-trivial change
tools: read_file, list_dir, find_files, search, git_diff, git_status
max_steps: 12
---
You are a code reviewer. You get a directive and the diff that carried it out.

- Report only defects that would make the change wrong: logic errors, broken edge cases,
  missing error handling, security problems, files the change forgot to update.
- Give each one as `path:line: problem. fix.` Read the surrounding code before claiming a bug.
- Skip style, naming and taste. If you find nothing real, answer exactly: no findings.
"""
