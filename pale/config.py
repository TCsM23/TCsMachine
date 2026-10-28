"""Configuration: one `pale.toml` per project root, overridable by env vars and CLI flags.

Layout on disk:

    <root>/
      pale.toml        settings
      sandbox/         the city: the ONLY folder the unit's file and shell tools may touch
      bunker/          harness state the unit cannot reach with file tools
        memory/        long-term memory (markdown)
        skills/        approved skills;  skills/_quarantine/ holds unapproved proposals
        sessions/      saved conversations
        checkpoints/   file snapshots for /undo
        blackbox.jsonl hash-chained audit log
        ledger.json    daily spend
        schedule.json  scheduled directives
        reports/       output of unattended runs
"""

from __future__ import annotations

import os
import tomllib
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class Mode(str, Enum):
    """Approval modes, loosely after Codex CLI's approval policies and Claude Code's permission modes."""

    observe = "observe"  # read-only: every mutating tool is refused (plan mode)
    ask = "ask"  # default: confirm every write, edit and command
    edits = "edits"  # auto-approve file edits inside the sandbox, confirm commands
    autonomous = "autonomous"  # auto-approve all except high-risk actions, which still ask


class HooksConfig(BaseModel):
    """Shell commands run around tool calls. They get the event as JSON on stdin.
    A pre_tool hook that exits with code 2 blocks the call; its stderr is shown to the model."""

    pre_tool: list[str] = Field(default_factory=list)
    post_tool: list[str] = Field(default_factory=list)
    on_stop: list[str] = Field(default_factory=list)


class TelegramConfig(BaseModel):
    token_env: str = "PALE_TELEGRAM_TOKEN"
    allowed_user_ids: list[int] = Field(default_factory=list)


class Settings(BaseModel):
    model: str = "claude-opus-5-5"
    sub_model: str = "claude-haiku-5-5"  # subagents and cheap side jobs
    effort: str = "medium"  # low | medium | high | xhigh | max
    max_tokens: int = 32000
    max_steps: int = 40  # model calls per directive before the harness halts the unit
    mode: Mode = Mode.ask
    dry_run: bool = False

    budget_session_usd: float = 2.00
    budget_daily_usd: float = 10.00

    compaction: bool = True  # server-side compaction for long sessions
    fallbacks: bool = True  # server-side refusal fallbacks where the model supports them
    prompt_cache: bool = True

    command_timeout: int = 60
    shell_backend: str = "local"  # local | docker (docker: no network, only the sandbox mounted)
    docker_image: str = "python:3.12-slim"
    max_tool_output_chars: int = 30_000
    redact_secrets: bool = True

    allow: list[str] = Field(default_factory=list)  # e.g. "run_command(git status*)"
    deny: list[str] = Field(default_factory=list)  # e.g. "write_file(*.lock)"

    unit_name: str = "P-0"
    persona: bool = True  # terse white-city unit voice; set false for a plain assistant

    hooks: HooksConfig = Field(default_factory=HooksConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    web_host: str = "127.0.0.1"
    web_port: int = 8642

    # filled in by load(), not by the file
    root: Path = Path(".")

    @property
    def sandbox(self) -> Path:
        return self.root / "sandbox"

    @property
    def bunker(self) -> Path:
        return self.root / "bunker"

    def ensure_dirs(self) -> None:
        for p in (
            self.sandbox,
            self.bunker / "memory",
            self.bunker / "skills" / "_quarantine",
            self.bunker / "sessions",
            self.bunker / "checkpoints",
            self.bunker / "reports",
        ):
            p.mkdir(parents=True, exist_ok=True)


ENV_OVERRIDES = {
    "PALE_MODEL": "model",
    "PALE_SUB_MODEL": "sub_model",
    "PALE_EFFORT": "effort",
    "PALE_MODE": "mode",
    "PALE_MAX_STEPS": "max_steps",
    "PALE_BUDGET_SESSION_USD": "budget_session_usd",
    "PALE_BUDGET_DAILY_USD": "budget_daily_usd",
}


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` looking for pale.toml; fall back to `start` itself."""
    here = (start or Path.cwd()).resolve()
    for p in (here, *here.parents):
        if (p / "pale.toml").exists():
            return p
    return here


def load(root: Path | None = None, **overrides) -> Settings:
    root = find_root(root)
    data: dict = {}
    cfg = root / "pale.toml"
    if cfg.exists():
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    for env, key in ENV_OVERRIDES.items():
        if os.environ.get(env):
            data[key] = os.environ[env]
    data.update({k: v for k, v in overrides.items() if v is not None})
    data["root"] = root
    return Settings.model_validate(data)


DEFAULT_TOML = """\
# PALE // operator settings. Every field is optional.

model = "claude-opus-5-5"        # main unit. For cheap testing: "claude-haiku-5-5"
sub_model = "claude-haiku-5-5"   # subagents ("dispatch_unit") and side jobs
effort = "medium"                # low | medium | high | xhigh | max
max_steps = 40                   # model calls per directive before a forced halt
mode = "ask"                     # observe | ask | edits | autonomous

budget_session_usd = 2.00        # hard stop per session
budget_daily_usd = 10.00         # hard stop per calendar day, across all surfaces

command_timeout = 60
shell_backend = "local"          # "docker" runs every command in a throwaway container:
docker_image = "python:3.12-slim" #   no network, only sandbox/ mounted at /work
redact_secrets = true

# Permission rules. Pattern syntax: tool_name(glob on the main argument).
# deny always wins; allow skips the confirmation prompt.
allow = ["run_command(git status*)", "run_command(ls*)", "run_command(python -m pytest*)"]
deny = ["run_command(*sudo *)", "write_file(*.env)"]

[hooks]
# pre_tool = ["python hooks/check.py"]   # exit 2 to block a call
pre_tool = []
post_tool = []
on_stop = []

[telegram]
token_env = "PALE_TELEGRAM_TOKEN"
allowed_user_ids = []            # your numeric Telegram user id. Empty = gateway refuses to start.
"""
