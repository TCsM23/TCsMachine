"""Operator hooks, after Claude Code's hooks: shell commands that receive an event as JSON on
stdin. For pre_tool hooks, exit code 2 blocks the call and stderr goes back to the model as the
reason. Any other non-zero exit is reported as a warning and the call proceeds.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class HookResult:
    blocked: bool = False
    message: str = ""


def run_hooks(commands: list[str], event: dict, cwd: Path, timeout: int = 30) -> HookResult:
    notes = []
    for cmd in commands:
        try:
            p = subprocess.run(cmd, shell=True, input=json.dumps(event, default=str), cwd=cwd,
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            notes.append(f"hook timed out: {cmd}")
            continue
        if p.returncode == 2:
            return HookResult(True, (p.stderr or p.stdout or f"blocked by hook: {cmd}").strip())
        if p.returncode != 0:
            notes.append(f"hook {cmd!r} exited {p.returncode}: {p.stderr.strip()[:300]}")
        elif p.stdout.strip():
            notes.append(p.stdout.strip()[:1000])
    return HookResult(False, "\n".join(notes))
