"""Sandbox confinement, risk scoring and the allow / ask / deny decision.

Order of checks (first match wins):
  1. hard-deny patterns (fork bombs, wiping disks)      -> deny, in every mode
  2. operator `deny` rules from pale.toml               -> deny
  3. submit_plan                                        -> ask in plan mode, deny otherwise
  4. observe or plan mode and the tool mutates          -> deny
  5. tool is read-only                                  -> allow
  6. high risk                                          -> ask, in every mode (even autonomous)
  7. operator `allow` rules, or "always" this session   -> allow
  8. edits mode and the tool is a file edit             -> allow
  9. autonomous mode                                    -> allow
 10. otherwise                                          -> ask
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .config import Mode, Settings


class SandboxViolation(Exception):
    pass


class Sandbox:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, path: str | Path) -> Path:
        """Map a model-supplied path into the sandbox. Absolute paths are read as sandbox-relative
        ("/notes.md" means sandbox/notes.md). Anything that escapes, including via symlinks, is refused."""
        p = Path(str(path).strip() or ".")
        if p.is_absolute():
            p = Path(*p.parts[1:]) if len(p.parts) > 1 else Path(".")
        full = (self.root / p).resolve()
        if full != self.root and self.root not in full.parents:
            raise SandboxViolation(f"path escapes the sandbox: {path}")
        return full

    def rel(self, full: Path) -> str:
        r = full.resolve().relative_to(self.root).as_posix()
        return r if r != "." else "."


# ---------------------------------------------------------------- command risk

HARD_DENY = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*(/|~|\$HOME)(\s|$|\*)", "deletes the root or home directory"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bmkfs(\.\w+)?\b", "formats a filesystem"),
    (r"\bdd\b.*\bof=/dev/", "writes raw to a device"),
    (r">\s*/dev/(sd|nvme|disk)", "writes raw to a disk"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "powers the machine off"),
    (r"\bchmod\s+-R\s+[0-7]*7[0-7]*\s+/(\s|$)", "opens permissions on the whole disk"),
]

HIGH = [
    (r"\bsudo\b|\bdoas\b|\bsu\s", "elevates privileges"),
    (r"\brm\s+-[a-zA-Z]*[rf]", "recursive or forced delete"),
    (r"\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b", "pipes a download into a shell"),
    (r"\bgit\s+push\b", "publishes outside the machine"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|restore\b)", "discards work"),
    (r"\b(pip|pip3|npm|pnpm|yarn|gem|cargo|brew|apt|apt-get)\s+(install|add|i)\b", "installs third-party code"),
    (r"\b(ssh|scp|rsync|nc|ncat|telnet|ftp)\b", "talks to another machine"),
    (r"\b(chmod|chown|chgrp)\b", "changes permissions"),
    (r"\b(kill|pkill|killall)\b", "stops processes"),
    (r"(^|[\s'\"=])\.\./", "reaches outside the working folder"),
    (r"(^|\s)(/|~)(?!dev/null)[\w.~-]", "uses an absolute path outside the sandbox"),
    (r"\beval\b|\bexec\b", "runs dynamically built code"),
    (r"\bcrontab\b|\bsystemctl\b|\blaunchctl\b", "changes system services"),
    (r"\b(env|printenv)\b|\$\{?[A-Z_]*(KEY|TOKEN|SECRET)", "reads environment secrets"),
]

READ_ONLY_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "wc", "echo", "grep", "rg", "find", "tree", "stat",
    "file", "du", "df", "date", "whoami", "which", "diff", "sort", "uniq", "less", "more",
}
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "blame", "ls-files", "rev-parse"}


@dataclass
class Risk:
    level: str  # low | medium | high | forbidden
    reasons: list[str] = field(default_factory=list)


def score_command(command: str) -> Risk:
    for pat, why in HARD_DENY:
        if re.search(pat, command):
            return Risk("forbidden", [why])
    reasons = [why for pat, why in HIGH if re.search(pat, command)]
    if reasons:
        return Risk("high", reasons)
    if re.search(r"[;&|`$()<>]", command.replace("2>&1", "")):
        return Risk("medium", ["chains, pipes or redirects"])
    try:
        argv = shlex.split(command)
    except ValueError:
        return Risk("medium", ["could not parse the command"])
    if not argv:
        return Risk("low", [])
    if argv[0] in READ_ONLY_COMMANDS:
        return Risk("low", ["read-only command"])
    if argv[0] == "git" and len(argv) > 1 and argv[1] in READ_ONLY_GIT:
        return Risk("low", ["read-only git command"])
    return Risk("medium", ["runs a program"])


# ---------------------------------------------------------------- decisions

@dataclass
class Decision:
    verdict: str  # allow | ask | deny
    reason: str
    rule: str = ""  # the pattern an "always" approval should remember


def rule_matches(pattern: str, tool: str, arg: str) -> bool:
    m = re.fullmatch(r"\s*([\w*]+)\s*(?:\((.*)\))?\s*", pattern)
    if not m:
        return False
    tool_pat, arg_pat = m.group(1), m.group(2)
    if not fnmatch.fnmatchcase(tool, tool_pat):
        return False
    return arg_pat is None or fnmatch.fnmatchcase(arg, arg_pat)


EDIT_TOOLS = {"write_file", "edit_file", "make_dir"}


def always_rule(tool: str, arg: str) -> str:
    """The rule an "always allow" answer stores. Commands generalize to their first two words
    ("npm test -- -x" -> "run_command(npm test*)"); file edits generalize to the tool."""
    if tool == "run_command":
        words = arg.split()[:2]
        return f"run_command({' '.join(words)}*)" if words else ""
    if tool in EDIT_TOOLS:
        return f"{tool}(*)"
    return f"{tool}({arg})"


class Policy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mode: Mode = settings.mode
        self.session_allow: list[str] = []

    def decide(self, tool: str, arg: str, mutates: bool, risk: Risk) -> Decision:
        if risk.level == "forbidden":
            return Decision("deny", f"forbidden: {', '.join(risk.reasons)}")
        for pat in self.settings.deny:
            if rule_matches(pat, tool, arg):
                return Decision("deny", f"denied by rule {pat}")
        if tool == "submit_plan":
            if self.mode == Mode.plan:
                return Decision("ask", "the operator reviews the plan before anything changes")
            return Decision("deny", "submit_plan is only used in plan mode")
        if not mutates:
            return Decision("allow", "read-only")
        if self.mode == Mode.observe:
            return Decision("deny", "observe mode: the unit may look but not touch")
        if self.mode == Mode.plan:
            return Decision("deny", "plan mode: nothing changes until the operator approves your plan (submit_plan)")
        if risk.level == "high":
            return Decision("ask", "high risk always needs the operator", "")
        for pat in [*self.settings.allow, *self.session_allow]:
            if rule_matches(pat, tool, arg):
                return Decision("allow", f"allowed by rule {pat}")
        if self.mode == Mode.edits and tool in EDIT_TOOLS:
            return Decision("allow", "edits mode")
        if self.mode == Mode.autonomous:
            return Decision("allow", "autonomous mode")
        return Decision("ask", "needs the operator", always_rule(tool, arg))

    def remember(self, rule: str) -> None:
        if rule and rule not in self.session_allow:
            self.session_allow.append(rule)
