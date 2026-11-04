"""Background jobs: long commands (dev servers, test suites, builds) that run detached while the
unit keeps working, after Claude Code's background bash.

A job is approved exactly like a foreground run_command; starting it in the background changes
nothing about the gate. Output goes to bunker/jobs/<id>.log, which the unit reads back through
job_status (redacted and clipped like any tool output). Every job has a hard lifetime
(`job_timeout`), and all jobs are killed when PALE exits.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Job:
    id: str
    command: str
    log: Path
    started: float
    proc: subprocess.Popen | None = None
    ended: float | None = None
    code: int | None = None
    stopped: str = ""  # "operator" | "timeout" | "" (exited by itself)
    container: str = ""  # docker backend: container name, killed explicitly
    done: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def running(self) -> bool:
        return self.ended is None

    def info(self) -> dict:
        return {"id": self.id, "command": self.command, "running": self.running, "code": self.code,
                "stopped": self.stopped, "started": self.started, "ended": self.ended,
                "seconds": round((self.ended or time.time()) - self.started, 1)}


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


class Jobs:
    def __init__(self, folder: Path, max_jobs: int = 4, timeout: int = 1800):
        self.folder = folder
        self.max_jobs = max_jobs
        self.timeout = timeout
        self.jobs: dict[str, Job] = {}
        self.listeners: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()

    def running(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.running]

    def start(self, command: str, argv: list[str] | str, *, shell: bool, cwd: Path, env: dict, container: str = "",
              job_id: str = "") -> Job:
        with self._lock:
            if len(self.running()) >= self.max_jobs:
                raise RuntimeError(f"{self.max_jobs} background jobs are already running; stop one first")
            self.folder.mkdir(parents=True, exist_ok=True)
            jid = job_id or "j" + uuid.uuid4().hex[:5]
            job = Job(jid, command, self.folder / f"{jid}.log", time.time(), container=container)
            out = open(job.log, "wb")
            kw: dict = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
            job.proc = subprocess.Popen(argv, shell=shell, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, **kw)
            out.close()
            self.jobs[jid] = job
        threading.Thread(target=self._watch, args=(job,), daemon=True, name=f"pale-job-{jid}").start()
        self._notify(job)
        return job

    def _watch(self, job: Job) -> None:
        try:
            job.code = job.proc.wait(self.timeout)
        except subprocess.TimeoutExpired:
            job.stopped = "timeout"
            self._kill(job)
            job.code = job.proc.wait()
        job.ended = time.time()
        job.done.set()
        self._notify(job)

    def _kill(self, job: Job) -> None:
        if job.container:
            subprocess.run(["docker", "kill", job.container], capture_output=True, timeout=20)
        _kill_tree(job.proc)

    def _notify(self, job: Job) -> None:
        for fn in list(self.listeners):
            try:
                fn(job.info())
            except Exception:
                pass

    def get(self, jid: str) -> Job | None:
        return self.jobs.get(jid)

    def stop(self, jid: str) -> Job:
        job = self.jobs.get(jid)
        if not job:
            raise KeyError(jid)
        if job.running:
            job.stopped = "operator"
            self._kill(job)
            job.done.wait(6)
        return job

    def tail(self, jid: str, chars: int = 6000) -> str:
        job = self.jobs[jid]
        try:
            with open(job.log, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - chars))
                data = f.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return ""
        return ("…" if size > chars else "") + data

    def stop_all(self) -> None:
        for job in self.running():
            try:
                self.stop(job.id)
            except Exception:
                pass


_registry: dict[str, Jobs] = {}
_reg_lock = threading.Lock()


def jobs_for(settings) -> Jobs:
    """One job table per workspace, shared by every agent in this process (jobs outlive sessions)."""
    key = str(settings.root.resolve())
    with _reg_lock:
        j = _registry.get(key)
        if not j:
            j = _registry[key] = Jobs(settings.bunker / "jobs", settings.max_jobs, settings.job_timeout)
        j.max_jobs, j.timeout = settings.max_jobs, settings.job_timeout
        return j


@atexit.register
def _shutdown() -> None:
    for j in list(_registry.values()):
        j.stop_all()
