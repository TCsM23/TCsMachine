"""Git tools. Read-only ones run freely; committing goes through the approval gate."""

from __future__ import annotations

import subprocess

from pydantic import BaseModel, Field

from ..permissions import Risk
from ..redact import scrubbed_env
from .base import Tool, ToolError
from .shell import clip


def git(ctx, *args: str, timeout: int = 30) -> str:
    if not (ctx.sandbox.root / ".git").exists():
        raise ToolError("the sandbox is not a git repository (run_command 'git init' first, with approval)")
    try:
        p = subprocess.run(["git", *args], cwd=ctx.sandbox.root, env={**scrubbed_env(), "GIT_TERMINAL_PROMPT": "0"},
                           capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise ToolError("git is not installed")
    except subprocess.TimeoutExpired:
        raise ToolError("git timed out")
    if p.returncode:
        raise ToolError(p.stderr.strip() or f"git exited {p.returncode}")
    return clip(p.stdout, ctx.settings.max_tool_output_chars) or "(nothing)"


class GitStatus(Tool):
    name = "git_status"
    description = "Show the sandbox repository's branch and changed files (git status --short --branch)."

    class Input(BaseModel):
        pass

    def arg(self, inp):
        return ""

    def run(self, inp, ctx):
        return git(ctx, "status", "--short", "--branch")


class GitDiff(Tool):
    name = "git_diff"
    description = "Show uncommitted changes (or staged ones), optionally for one path."

    class Input(BaseModel):
        path: str = ""
        staged: bool = False

    def run(self, inp, ctx):
        args = ["diff", "--no-color"] + (["--staged"] if inp.staged else [])
        if inp.path:
            args += ["--", ctx.sandbox.rel(ctx.sandbox.resolve(inp.path))]
        return git(ctx, *args)


class GitLog(Tool):
    name = "git_log"
    description = "Show recent commits, one line each."

    class Input(BaseModel):
        n: int = Field(15, ge=1, le=200)

    def arg(self, inp):
        return str(inp.n)

    def run(self, inp, ctx):
        return git(ctx, "log", f"-{inp.n}", "--oneline", "--decorate", "--no-color")


class GitCommit(Tool):
    name = "git_commit"
    description = "Stage all changes (or only listed paths) and commit them with a message. Never pushes."
    mutates = True
    parallel_safe = False

    class Input(BaseModel):
        message: str
        paths: list[str] = Field(default_factory=list, description="empty = stage everything")

    def risk(self, inp, ctx):
        return Risk("medium", ["records a commit in the sandbox repository"])

    def preview(self, inp, ctx):
        try:
            stat = git(ctx, "status", "--short")
        except ToolError as e:
            stat = str(e)
        return f"commit message:\n  {inp.message}\n\nchanges:\n{stat}"

    def run(self, inp, ctx):
        paths = [ctx.sandbox.rel(ctx.sandbox.resolve(p)) for p in inp.paths] or ["-A"]
        git(ctx, "add", *(["--"] + paths if inp.paths else paths))
        return git(ctx, "-c", "user.name=PALE unit", "-c", "user.email=unit@pale.local", "commit", "-m", inp.message)


TOOLS = [GitStatus(), GitDiff(), GitLog(), GitCommit()]
