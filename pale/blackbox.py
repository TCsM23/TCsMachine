"""The black box: an append-only, hash-chained action log.

Every line carries the SHA-256 of the previous line, so editing or deleting an entry after
the fact breaks the chain and `pale audit verify` names the first broken line. Most
harnesses log actions; few make the log tamper-evident.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

GENESIS = "0" * 64


def _digest(entry: dict) -> str:
    body = json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


class BlackBox:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._last = self._tail_hash()

    def _tail_hash(self) -> str:
        if not self.path.exists():
            return GENESIS
        last = GENESIS
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    def record(self, kind: str, session: str = "", **data) -> None:
        with self._lock:
            entry = {"ts": round(time.time(), 3), "kind": kind, "session": session, "data": data, "prev": self._last}
            entry["hash"] = _digest(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            self._last = entry["hash"]

    def tail(self, n: int = 20, session: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        rows = [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if session:
            rows = [r for r in rows if r.get("session") == session]
        return rows[-n:]


def verify(path: Path) -> tuple[bool, int, str]:
    """Returns (ok, entries_checked, message)."""
    if not path.exists():
        return True, 0, "no log yet"
    prev = GENESIS
    n = 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return False, n, f"line {n}: not valid JSON"
        claimed = entry.pop("hash", None)
        if entry.get("prev") != prev:
            return False, n, f"line {n}: chain broken (an entry before it was removed or altered)"
        if _digest(entry) != claimed:
            return False, n, f"line {n}: contents altered after recording"
        prev = claimed
    return True, n, "chain intact"
