"""Scheduled directives (Hermes scheduler / cron-style automations).

Specs:  "every 30m" | "every 2h" | "daily 09:00" | "weekdays 08:30" | "once 2026-11-01 09:00"

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
        nxt = next_run(spec, datetime.now())
        if nxt is None:
            raise ValueError("that time is already in the past")
        task = {
            "id": uuid.uuid4().hex[:8],
            "name": name or directive[:40],
            "spec": spec,
            "directive": directive,
            "next": nxt.isoformat(timespec="minutes"),
            "last": None,
            "enabled": True,
        }
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
                if t["enabled"] and t["next"] and datetime.fromisoformat(t["next"]) <= now:
                    fired.append(dict(t))
                    t["last"] = now.isoformat(timespec="minutes")
                    nxt = next_run(t["spec"], now)
                    t["next"] = nxt.isoformat(timespec="minutes") if nxt else None
                    t["enabled"] = nxt is not None
            if fired:
                self.save(tasks)
            return fired


class Scheduler(threading.Thread):
    def __init__(self, store: ScheduleStore, run: Callable[[dict], str], tick: float = 20.0):
        super().__init__(daemon=True, name="pale-scheduler")
        self.store = store
        self.run_task = run
        self.tick = tick
        self.notifiers: list[Callable[[dict, str], None]] = []
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            for task in self.store.due(datetime.now()):
                threading.Thread(target=self._fire, args=(task,), daemon=True).start()
            self._stop.wait(self.tick)

    def _fire(self, task: dict) -> None:
        try:
            report = self.run_task(task)
        except Exception as e:  # a failing directive must not kill the scheduler
            report = f"directive failed: {e}"
        for notify in self.notifiers:
            try:
                notify(task, report)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
