"""Built-in updater. No separate updater program: the app updates itself.

1. check     GET https://api.github.com/repos/<repo>/releases/latest and compare its tag
             (v1.2.3) with the running version.
2. ask       the UI shows the release notes: Install & restart, Later, or Skip this version.
3. download  the installer for this platform, streamed to the app folder.
4. verify    its SHA-256 must match the line for it in the release's SHA256SUMS.txt.
             If the file is missing or doesn't match, the update is refused.
5. install   Windows: run the downloaded Setup silently with /UPDATE=1, then quit. The
             installer replaces the files and relaunches PALE.
             Other platforms, or when running from source: open the release page instead.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from .. import __version__

API = "https://api.github.com/repos/{repo}/releases/latest"
SUMS = "SHA256SUMS.txt"


def parse_version(tag: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(tag: str, current: str = __version__) -> bool:
    a, b = parse_version(tag), parse_version(current)
    return bool(a and b and a > b)


def asset_pattern(platform: str = sys.platform) -> str:
    if platform == "win32":
        return r"^PALE-Setup-[\d.]+\.exe$"
    if platform == "darwin":
        return r"^PALE-[\d.]+-macos\.zip$"
    return r"^PALE-[\d.]+-linux\.tar\.gz$"


def can_self_install() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)


@dataclass
class UpdateState:
    status: str = "idle"  # idle | checking | current | available | downloading | verifying | ready | installing | error
    current: str = __version__
    latest: str = ""
    notes: str = ""
    published: str = ""
    page: str = ""
    asset: str = ""
    size: int = 0
    progress: float = 0.0
    error: str = ""
    last_check: float = 0.0
    self_install: bool = field(default_factory=can_self_install)
    skipped: bool = False


class Updater:
    def __init__(self, repo_fn: Callable[[], str], folder: Path, skipped_fn: Callable[[], str] = lambda: "",
                 http: httpx.Client | None = None, quit_fn: Callable[[], None] | None = None):
        self.repo_fn = repo_fn
        self.skipped_fn = skipped_fn
        self.folder = folder / "updates"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.http = http or httpx.Client(timeout=30, follow_redirects=True,
                                         headers={"User-Agent": f"PALE/{__version__}", "Accept": "application/vnd.github+json"})
        self.quit_fn = quit_fn
        self.state = UpdateState()
        self._release: dict = {}
        self._lock = threading.Lock()
        self.listeners: list[Callable[[dict], None]] = []

    def _set(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self.state, k, v)
        snap = self.snapshot()
        for fn in self.listeners:
            try:
                fn(snap)
            except Exception:
                pass

    def snapshot(self) -> dict:
        return asdict(self.state)

    # ------------------------------------------------------------ check
    def check(self) -> dict:
        repo = self.repo_fn()
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
            self._set(status="error", error="no update repository configured")
            return self.snapshot()
        self._set(status="checking", error="")
        try:
            r = self.http.get(API.format(repo=repo))
            if r.status_code == 404:
                self._set(status="current", error="", latest="", last_check=time.time())
                return self.snapshot()  # no releases yet (or the repo is private)
            r.raise_for_status()
            rel = r.json()
        except (httpx.HTTPError, ValueError) as e:
            self._set(status="error", error=f"update check failed: {e}", last_check=time.time())
            return self.snapshot()
        tag = rel.get("tag_name", "")
        asset = next((a for a in rel.get("assets", []) if re.match(asset_pattern(), a["name"])), None)
        self._release = rel
        if not newer(tag) or rel.get("draft") or rel.get("prerelease"):
            self._set(status="current", latest=tag.lstrip("v"), last_check=time.time())
            return self.snapshot()
        self._set(status="available", latest=tag.lstrip("v"), notes=rel.get("body") or "",
                  published=rel.get("published_at", ""), page=rel.get("html_url", ""),
                  asset=asset["name"] if asset else "", size=asset["size"] if asset else 0,
                  skipped=self.skipped_fn() == tag.lstrip("v"), last_check=time.time(), progress=0.0)
        return self.snapshot()

    # ------------------------------------------------------------ download + verify
    def _expected_hash(self, name: str) -> str:
        sums = next((a for a in self._release.get("assets", []) if a["name"] == SUMS), None)
        if not sums:
            raise RuntimeError(f"release has no {SUMS}; refusing an unverifiable update")
        text = self.http.get(sums["browser_download_url"]).text
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == name:
                return parts[0].lower()
        raise RuntimeError(f"{name} is not listed in {SUMS}")

    def download(self) -> Path:
        with self._lock:
            asset = next((a for a in self._release.get("assets", []) if a["name"] == self.state.asset), None)
            if not asset:
                raise RuntimeError("no installer for this platform in the latest release")
            expected = self._expected_hash(asset["name"])
            dest = self.folder / asset["name"]
            part = dest.with_suffix(dest.suffix + ".part")
            h = hashlib.sha256()
            self._set(status="downloading", progress=0.0, error="")
            with self.http.stream("GET", asset["browser_download_url"]) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or asset.get("size") or 0)
                done = 0
                last = 0.0
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        if total and time.time() - last > 0.2:
                            self._set(progress=done / total)
                            last = time.time()
            self._set(status="verifying", progress=1.0)
            if h.hexdigest() != expected:
                part.unlink(missing_ok=True)
                raise RuntimeError("checksum mismatch: the download is corrupt or was tampered with")
            part.replace(dest)
            self._set(status="ready")
            return dest

    # ------------------------------------------------------------ install
    def install(self) -> dict:
        """Download, verify and launch the installer (Windows), or open the release page."""
        if not self.state.self_install:
            if self.state.page:
                webbrowser.open(self.state.page)
            self._set(status="available", error="")
            return self.snapshot()
        try:
            path = self.download()
        except Exception as e:
            self._set(status="error", error=str(e))
            return self.snapshot()
        self._set(status="installing")
        log = self.folder / "install.log"
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1", f"/LOG={log}"],
                         creationflags=flags, close_fds=True)
        if self.quit_fn:
            threading.Timer(1.0, self.quit_fn).start()
        return self.snapshot()

    def start_background(self, every_hours: float = 6.0, enabled: Callable[[], bool] = lambda: True) -> None:
        def loop():
            time.sleep(4)
            while True:
                if enabled():
                    self.check()
                time.sleep(every_hours * 3600)

        threading.Thread(target=loop, daemon=True, name="pale-updater").start()
