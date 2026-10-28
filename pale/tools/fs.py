"""File tools, all confined to the sandbox.

Borrowed rules:
  - line-numbered reads with offset/limit (Claude Code Read)
  - exact-string edits that must match once (Claude Code Edit, Aider search/replace blocks)
  - read-before-write: an existing file must have been read in this session and be unchanged
    since, or the write is refused. Stops the unit clobbering edits made behind its back.
"""

from __future__ import annotations

import difflib
import fnmatch
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..permissions import Risk
from .base import Tool, ToolContext, ToolError

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build"}
MAX_READ_LINES = 2000


def _is_text(p: Path) -> bool:
    try:
        return b"\0" not in p.open("rb").read(4096)
    except OSError:
        return False


def _walk(root: Path):
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def unified_diff(old: str, new: str, name: str) -> str:
    d = difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{name}", f"b/{name}", n=2)
    out = "".join(d)
    return out if len(out) < 8000 else out[:8000] + "\n... (diff truncated)"


def _check_fresh(ctx: ToolContext, full: Path) -> None:
    if not full.exists():
        return
    stamp = ctx.agent.read_stamps.get(str(full))
    if stamp is None:
        raise ToolError(f"read {ctx.sandbox.rel(full)} with read_file before changing it")
    if full.stat().st_mtime_ns != stamp:
        raise ToolError(f"{ctx.sandbox.rel(full)} changed since you read it; read it again first")


# ------------------------------------------------------------------ read-only

class ReadFile(Tool):
    name = "read_file"
    description = (
        "Read a text file in the sandbox. Returns numbered lines (cat -n style). Use offset and limit "
        "for large files. You must read a file before you edit or overwrite it."
    )

    class Input(BaseModel):
        path: str = Field(description="Path relative to the sandbox root")
        offset: int = Field(1, ge=1, description="First line to return (1-based)")
        limit: int = Field(MAX_READ_LINES, ge=1, le=MAX_READ_LINES, description="Number of lines")

    def run(self, inp, ctx):
        full = ctx.sandbox.resolve(inp.path)
        if not full.is_file():
            raise ToolError(f"no such file: {inp.path}")
        if not _is_text(full):
            raise ToolError(f"{inp.path} looks binary; not reading it")
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        ctx.agent.read_stamps[str(full)] = full.stat().st_mtime_ns
        chunk = lines[inp.offset - 1 : inp.offset - 1 + inp.limit]
        body = "\n".join(f"{i:6}\t{l}" for i, l in enumerate(chunk, start=inp.offset))
        tail = ""
        if inp.offset - 1 + inp.limit < len(lines):
            tail = f"\n... ({len(lines)} lines total; continue with offset={inp.offset + inp.limit})"
        return (body or "(empty file)") + tail


class ListDir(Tool):
    name = "list_dir"
    description = "List a sandbox directory as a tree. Skips .git, node_modules, caches and virtualenvs."

    class Input(BaseModel):
        path: str = Field(".", description="Directory relative to the sandbox root")
        depth: int = Field(2, ge=1, le=6)

    def run(self, inp, ctx):
        root = ctx.sandbox.resolve(inp.path)
        if not root.is_dir():
            raise ToolError(f"not a directory: {inp.path}")
        out = [f"{ctx.sandbox.rel(root)}/"]
        count = 0
        for p in _walk(root):
            rel = p.relative_to(root)
            if len(rel.parts) > inp.depth:
                continue
            count += 1
            if count > 500:
                out.append("... (truncated at 500 entries)")
                break
            size = "" if p.is_dir() else f"  ({p.stat().st_size} B)"
            out.append("  " * (len(rel.parts) - 1) + f"{p.name}{'/' if p.is_dir() else ''}{size}")
        return "\n".join(out) if count else out[0] + "\n  (empty)"


class FindFiles(Tool):
    name = "find_files"
    description = "Find files by glob pattern, e.g. '**/*.py' or 'src/*.md'. Returns sandbox-relative paths."

    class Input(BaseModel):
        pattern: str
        path: str = "."

    def run(self, inp, ctx):
        root = ctx.sandbox.resolve(inp.path)
        hits = [ctx.sandbox.rel(p) for p in root.glob(inp.pattern) if p.is_file()
                and not any(part in SKIP_DIRS for part in p.relative_to(root).parts)]
        return "\n".join(hits[:300]) + ("\n... (more)" if len(hits) > 300 else "") if hits else "no matches"


class Search(Tool):
    name = "search"
    description = "Search file contents with a regular expression. Returns path:line: text for each match."

    class Input(BaseModel):
        pattern: str = Field(description="Python regular expression")
        path: str = "."
        glob: str = Field("*", description="Only files whose name matches this glob, e.g. '*.py'")
        ignore_case: bool = False

    def run(self, inp, ctx):
        try:
            rx = re.compile(inp.pattern, re.I if inp.ignore_case else 0)
        except re.error as e:
            raise ToolError(f"bad regex: {e}")
        root = ctx.sandbox.resolve(inp.path)
        files = [root] if root.is_file() else [p for p in _walk(root) if p.is_file()]
        out = []
        for p in files:
            if not fnmatch.fnmatch(p.name, inp.glob) or not _is_text(p):
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    out.append(f"{ctx.sandbox.rel(p)}:{i}: {line[:240]}")
                    if len(out) >= 200:
                        return "\n".join(out) + "\n... (stopped at 200 matches)"
        return "\n".join(out) or "no matches"


# ------------------------------------------------------------------ mutating

class WriteFile(Tool):
    name = "write_file"
    description = (
        "Create a file or overwrite it completely. Prefer edit_file for changes to existing files. "
        "Overwriting requires that you read the file first."
    )
    mutates = True
    parallel_safe = False

    class Input(BaseModel):
        path: str
        content: str

    def risk(self, inp, ctx):
        full = ctx.sandbox.resolve(inp.path)
        if full.exists():
            return Risk("medium", ["overwrites an existing file"])
        return Risk("low", ["creates a new file"])

    def preview(self, inp, ctx):
        full = ctx.sandbox.resolve(inp.path)
        old = full.read_text(encoding="utf-8", errors="replace") if full.is_file() else ""
        return unified_diff(old, inp.content, ctx.sandbox.rel(full))

    def run(self, inp, ctx):
        full = ctx.sandbox.resolve(inp.path)
        if full.is_dir():
            raise ToolError(f"{inp.path} is a directory")
        _check_fresh(ctx, full)
        ctx.agent.checkpoints.snapshot(full)
        full.parent.mkdir(parents=True, exist_ok=True)
        existed = full.exists()
        full.write_text(inp.content, encoding="utf-8")
        ctx.agent.read_stamps[str(full)] = full.stat().st_mtime_ns
        return f"{'overwrote' if existed else 'created'} {ctx.sandbox.rel(full)} ({len(inp.content.splitlines())} lines)"

    def dry(self, inp, ctx):
        return f"[dry run] would write {inp.path}. Diff:\n{self.preview(inp, ctx) or '(no change)'}"


class EditFile(Tool):
    name = "edit_file"
    description = (
        "Replace an exact string in a file. old_string must match exactly once (include surrounding "
        "lines to make it unique) unless replace_all is true. Read the file first."
    )
    mutates = True
    parallel_safe = False

    class Input(BaseModel):
        path: str
        old_string: str
        new_string: str
        replace_all: bool = False

    def _apply(self, inp, ctx) -> tuple[Path, str, str]:
        full = ctx.sandbox.resolve(inp.path)
        if not full.is_file():
            raise ToolError(f"no such file: {inp.path}")
        old = full.read_text(encoding="utf-8")
        n = old.count(inp.old_string)
        if not inp.old_string or n == 0:
            raise ToolError("old_string not found. Re-read the file and copy the text exactly, including whitespace.")
        if n > 1 and not inp.replace_all:
            raise ToolError(f"old_string matches {n} times; add surrounding lines to make it unique, or set replace_all")
        new = old.replace(inp.old_string, inp.new_string) if inp.replace_all else old.replace(inp.old_string, inp.new_string, 1)
        return full, old, new

    def risk(self, inp, ctx):
        return Risk("low", ["targeted edit"])

    def preview(self, inp, ctx):
        try:
            full, old, new = self._apply(inp, ctx)
        except ToolError as e:
            return str(e)
        return unified_diff(old, new, ctx.sandbox.rel(full))

    def run(self, inp, ctx):
        full, _, new = self._apply(inp, ctx)
        _check_fresh(ctx, full)
        ctx.agent.checkpoints.snapshot(full)
        full.write_text(new, encoding="utf-8")
        ctx.agent.read_stamps[str(full)] = full.stat().st_mtime_ns
        return f"edited {ctx.sandbox.rel(full)}"

    def dry(self, inp, ctx):
        return f"[dry run] would edit {inp.path}. Diff:\n{self.preview(inp, ctx)}"


class DeleteFile(Tool):
    name = "delete_file"
    description = "Delete one file (not directories). Recoverable with the operator's /undo."
    mutates = True
    parallel_safe = False

    class Input(BaseModel):
        path: str

    def risk(self, inp, ctx):
        return Risk("medium", ["deletes a file"])

    def run(self, inp, ctx):
        full = ctx.sandbox.resolve(inp.path)
        if not full.is_file():
            raise ToolError(f"no such file: {inp.path}")
        ctx.agent.checkpoints.snapshot(full)
        full.unlink()
        return f"deleted {ctx.sandbox.rel(full)}"


TOOLS = [ReadFile(), ListDir(), FindFiles(), Search(), WriteFile(), EditFile(), DeleteFile()]
