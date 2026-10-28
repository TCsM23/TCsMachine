"""Long-term memory and skills: plain markdown files with a small frontmatter header.

Memory  (Hermes / Claude Code memory): facts the unit keeps across sessions. The index
        (name + one-line description) goes in every system prompt; bodies load on `recall`.
Skills  (Hermes self-made skills / Agent Skills): reusable procedures. The unit can only
        *propose* a skill. Proposals land in skills/_quarantine/ and stay inert until the
        operator approves them, so one bad run can't poison every later run.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    s = SLUG.sub("-", name.lower()).strip("-")[:60]
    if not s:
        raise ValueError("name must contain letters or digits")
    return s


@dataclass
class Note:
    name: str
    description: str
    body: str
    meta: dict
    path: Path


def parse(path: Path) -> Note:
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = text
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        for line in head.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
    return Note(meta.get("name", path.stem), meta.get("description", ""), body.strip(), meta, path)


def render(name: str, description: str, body: str, **extra) -> str:
    head = {"name": name, "description": description.replace("\n", " "), **extra}
    lines = "\n".join(f"{k}: {v}" for k, v in head.items())
    return f"---\n{lines}\n---\n\n{body.strip()}\n"


class NoteStore:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[Note]:
        return [parse(p) for p in sorted(self.folder.glob("*.md"))]

    def get(self, name: str) -> Note | None:
        p = self.folder / f"{slugify(name)}.md"
        return parse(p) if p.exists() else None

    def put(self, name: str, description: str, body: str, **extra) -> Note:
        slug = slugify(name)
        p = self.folder / f"{slug}.md"
        p.write_text(render(slug, description, body, updated=time.strftime("%Y-%m-%d"), **extra), encoding="utf-8")
        return parse(p)

    def delete(self, name: str) -> bool:
        p = self.folder / f"{slugify(name)}.md"
        if p.exists():
            p.unlink()
            return True
        return False

    def index(self) -> str:
        return "\n".join(f"- {n.name}: {n.description}" for n in self.all())

    def search(self, query: str, limit: int = 5) -> list[Note]:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        scored = []
        for n in self.all():
            hay = f"{n.name} {n.description} {n.body}".lower()
            score = sum(hay.count(w) for w in words) + 3 * sum(w in n.name for w in words)
            if score:
                scored.append((score, n))
        return [n for _, n in sorted(scored, key=lambda x: -x[0])[:limit]]


class Memory(NoteStore):
    pass


class Skills(NoteStore):
    def __init__(self, folder: Path):
        super().__init__(folder)
        self.quarantine = NoteStore(folder / "_quarantine")

    def propose(self, name: str, description: str, body: str, session: str = "") -> Note:
        return self.quarantine.put(name, description, body, proposed_by=session or "unit", status="quarantined")

    def approve(self, name: str) -> Note:
        n = self.quarantine.get(name)
        if not n:
            raise KeyError(name)
        note = self.put(n.name, n.description, n.body, approved=time.strftime("%Y-%m-%d"))
        self.quarantine.delete(name)
        return note

    def reject(self, name: str) -> bool:
        return self.quarantine.delete(name)
