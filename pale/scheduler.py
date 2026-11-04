"""Scheduled directives (Hermes scheduler / cron-style automations).

Specs:  "every 30m" | "every 2h" | "daily 09:00" | "weekdays 08:30" | "once 2026-11-01 09:00"
        "watch src/**/*.py, tests/*.py"   (fires when matching sandbox files change)

Watchers re-read the files after their own run finishes, so a directive that edits the files it
watches does not trigger itself. Each watcher also has a cooldown and an hourly cap.

Unattended runs use the deny-all approver: anything that would need the operator is refused
and reported, never silently escalated. Reports land in bunker/reports/ and go to any
registered notifier (the Telegram gateway registers one).
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

SPEC = re.compile(
    r"^\s*(?:every\s+(?P<n>\d+)\s*(?P<unit>[mhd])|(?P<days>daily|weekdays)\s+(?P<hm>\d{1,2}:\d{2})"
    r"|once\s+(?P<at>\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}))\s*$",
    re.I,
)


WATCH = re.compile(r"^\s*watch\s+(?P<globs>\S.*)$", re.I)
WATCH_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
WATCH_COOLDOWN = 120  # seconds between two runs of one watcher
WATCH_PER_HOUR = 10
WATCH_MAX_FILES = 5000


def watch_globs(spec: str) -> list[str] | None:
    m = WATCH.match(spec)
    if not m:
        return None
    globs = [g.strip() for g in m["globs"].split(",") if g.strip()]
    for g in globs:
        if g.startswith(("/", "\\")) or ".." in g.replace("\\", "/").split("/"):
            raise ValueError(f"watch patterns are relative to the sandbox: {g!r}")
    return globs


def snapshot(root: Path, globs: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for g in globs:
        for p in root.glob(g):
            rel = p.relative_to(root)
            if WATCH_SKIP & set(rel.parts) or not p.is_file():
                continue
            try:
                out[rel.as_posix()] = p.stat().st_mtime
            except OSError:
                continue
            if len(out) >= WATCH_MAX_FILES:
                return out
    return out


def changed(before: dict[str, float], after: dict[str, float]) -> list[str]:
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def next_run(spec: str, after: datetime) -> datetime | None:
    m = SPEC.match(spec)
    if not m:
        raise ValueError(f"bad schedule spec: {spec!r} (try 'every 30m', 'daily 09:00', 'once 2026-11-01 09:00')")
    if m["n"]:
        step = {"m": "minutes", "h": "hours", "d": "days"}[m["unit"].lower()]
        return after + timedelta(**{step: int(m["n"])})
    if m["at"]:
        at = datetime.fromisoformat(m["at"].replace(" ", "T"))
        return at if at > after else None
    hh, mm = map(int, m["hm"].split(":"))
    cand = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= after:
        cand += timedelta(days=1)
    while m["days"].lower() == "weekdays" and cand.weekday() >= 5:
        cand += timedelta(days=1)
    return cand


class ScheduleStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save(self, tasks: list[dict]) -> None:
        self.path.write_text(json.dumps(tasks, indent=1))

    def add(self, spec: str, directive: str, name: str = "") -> dict:
        globs = watch_globs(spec)
        if globs is None:
            nxt = next_run(spec, datetime.now())
            if nxt is None:
                raise ValueError("that time is already in the past")
        if not directive.strip():
            raise ValueError("the directive is empty")
        task = {
            "id": uuid.uuid4().hex[:8],
            "name": name or directive[:40],
            "spec": spec,
            "directive": directive,
            "next": None if globs is not None else nxt.isoformat(timespec="minutes"),
            "last": None,
            "enabled": True,
        }
        if globs is not None:
            task["watch"] = globs
        with self._lock:
            tasks = self.load()
            tasks.append(task)
            self.save(tasks)
        return task

    def remove(self, task_id: str) -> bool:
        with self._lock:
            tasks = self.load()
            keep = [t for t in tasks if t["id"] != task_id]
            self.save(keep)
            return len(keep) != len(tasks)

    def due(self, now: datetime) -> list[dict]:
        """Return due tasks and advance their next-run time (so a crash can't double-fire)."""
        with self._lock:
            tasks = self.load()
            fired = []
            for t in tasks:
                if t.get("watch"):
                    continue
                if t["enabled"] and t["next"] and datetime.fromisoformat(t["next"]) <= now:
                    fired.append(dict(t))
                    t["last"] = now.isoformat(timespec="minutes")
                    nxt = next_run(t["spec"], now)
                    t["next"] = nxt.isoformat(timespec="minutes") if nxt else None
                    t["enabled"] = nxt is not None
            if fired:
                self.save(tasks)
            return fired

    def mark(self, task_id: str, when: datetime) -> None:
        with self._lock:
            tasks = self.load()
            for t in tasks:
                if t["id"] == task_id:
                    t["last"] = when.isoformat(timespec="minutes")
            self.save(tasks)


class Scheduler(threading.Thread):
    def __init__(self, store: ScheduleStore, run: Callable[[dict], str], tick: float = 5.0, watch_root: Path | None = None):
        super().__init__(daemon=True, name="pale-scheduler")
        self.store = store
        self.run_task = run
        self.tick = tick
        self.watch_root = watch_root
        self.notifiers: list[Callable[[dict, str], None]] = []
        self._stop = threading.Event()
        self._snaps: dict[str, dict[str, float]] = {}
        self._fires: dict[str, list[float]] = {}
        self._running: set[str] = set()

    def run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self.tick)

    def poll(self) -> None:
        now = datetime.now()
        for task in self.store.due(now):
            threading.Thread(target=self._fire, args=(task,), daemon=True).start()
        if self.watch_root:
            for task in self.store.load():
                if task.get("watch") and task.get("enabled", True):
                    self._check_watch(task)

    def _check_watch(self, task: dict) -> None:
        tid = task["id"]
        if tid in self._running:
            return
        snap = snapshot(self.watch_root, task["watch"])
        if tid not in self._snaps:  # first sight: baseline only
            self._snaps[tid] = snap
            return
        diff = changed(self._snaps[tid], snap)
        if not diff:
            return
        t = time.time()
        recent = [x for x in self._fires.get(tid, []) if t - x < 3600]
        if (recent and t - recent[-1] < WATCH_COOLDOWN) or len(recent) >= WATCH_PER_HOUR:
            return  # keep the old baseline: the change fires once the cooldown ends
        self._fires[tid] = recent + [t]
        self._running.add(tid)
        self.store.mark(tid, datetime.now())
        threading.Thread(target=self._fire, args=({**task, "changed": diff[:50]},), daemon=True).start()

    def _fire(self, task: dict) -> None:
        try:
            report = self.run_task(task)
        except Exception as e:  # a failing directive must not kill the scheduler
            report = f"directive failed: {e}"
        finally:
            if task.get("watch") and self.watch_root:
                self._snaps[task["id"]] = snapshot(self.watch_root, task["watch"])  # ignore the run's own edits
                self._running.discard(task["id"])
        for notify in self.notifiers:
            try:
                notify(task, report)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
