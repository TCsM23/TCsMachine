"""Checkpoints: every file mutation is snapshotted first, grouped by directive, so /undo
rolls back the whole last directive without needing git (Aider undoes via git commits,
Claude Code via its own checkpoints; this works in any folder).
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path


class Checkpoints:
    def __init__(self, store: Path, sandbox: Path):
        self.store = store
        self.sandbox = sandbox.resolve()
        self.current: str | None = None

    def begin(self, label: str) -> str:
        cid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        d = self.store / cid
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"label": label[:200], "files": {}, "ts": time.time()}))
        self.current = cid
        return cid

    def _meta(self, cid: str) -> dict:
        return json.loads((self.store / cid / "meta.json").read_text())

    def snapshot(self, path: Path) -> None:
        """Call before changing `path`. Only the first snapshot per directive counts."""
        if not self.current:
            return
        rel = path.resolve().relative_to(self.sandbox).as_posix()
        d = self.store / self.current
        meta = self._meta(self.current)
        if rel in meta["files"]:
            return
        if path.exists() and path.is_file():
            blob = uuid.uuid4().hex
            shutil.copy2(path, d / blob)
            meta["files"][rel] = blob
        else:
            meta["files"][rel] = None  # did not exist: undo deletes it
        (d / "meta.json").write_text(json.dumps(meta))

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.store.iterdir(), reverse=True) if self.store.exists() else []:
            if (d / "meta.json").exists():
                m = self._meta(d.name)
                if m["files"]:
                    out.append({"id": d.name, "label": m["label"], "files": list(m["files"]), "ts": m["ts"]})
        return out

    def undo(self, cid: str | None = None) -> dict:
        items = self.list()
        if not items:
            return {"id": None, "restored": []}
        target = next((i for i in items if i["id"] == cid), None) if cid else items[0]
        if not target:
            raise KeyError(cid)
        d = self.store / target["id"]
        meta = self._meta(target["id"])
        restored = []
        for rel, blob in meta["files"].items():
            dest = self.sandbox / rel
            if blob is None:
                if dest.exists():
                    dest.unlink()
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(d / blob, dest)
            restored.append(rel)
        shutil.rmtree(d)
        return {"id": target["id"], "label": target["label"], "restored": restored}
