"""Desktop app state that lives outside any workspace: the workspace list, update preferences,
and the API key.

Locations:
  Windows  %APPDATA%\\PALE
  macOS    ~/Library/Application Support/PALE
  Linux    $XDG_CONFIG_HOME/pale  (default ~/.config/pale)

The API key goes to the OS credential store (Windows Credential Manager, macOS Keychain,
Secret Service on Linux) through `keyring` when it's available. Otherwise it goes to a file
readable only by you.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from .. import UPDATE_REPO

KEYRING_SERVICE = "PALE"
KEYRING_USER = "anthropic_api_key"


def app_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "PALE"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "PALE"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pale"
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_workspace() -> Path:
    return Path.home() / "PALE" / "sector-01"


DEFAULTS = {
    "workspaces": [],  # [{"name": str, "path": str}]
    "active": "",
    "setup_done": False,
    "update": {"repo": "", "auto_check": True, "skipped": "", "last_check": 0},
}


class Store:
    def __init__(self, folder: Path | None = None):
        self.folder = folder or app_dir()
        self.path = self.folder / "desktop.json"
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            d = {}
        merged = json.loads(json.dumps(DEFAULTS))
        merged.update({k: v for k, v in d.items() if k != "update"})
        merged["update"].update(d.get("update", {}))
        return merged

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    # ---- workspaces
    def add_workspace(self, path: Path, name: str = "") -> dict:
        path = path.resolve()
        for w in self.data["workspaces"]:
            if Path(w["path"]) == path:
                return w
        w = {"name": name or path.name, "path": str(path)}
        self.data["workspaces"].append(w)
        self.save()
        return w

    def remove_workspace(self, path: str) -> None:
        self.data["workspaces"] = [w for w in self.data["workspaces"] if w["path"] != path]
        if self.data["active"] == path:
            self.data["active"] = self.data["workspaces"][0]["path"] if self.data["workspaces"] else ""
        self.save()

    @property
    def update_repo(self) -> str:
        return os.environ.get("PALE_UPDATE_REPO") or self.data["update"].get("repo") or UPDATE_REPO

    # ---- API key
    def _keyring(self):
        try:
            import keyring

            keyring.get_keyring()
            return keyring
        except Exception:
            return None

    def _key_file(self) -> Path:
        return self.folder / "credentials.json"

    def get_api_key(self) -> str:
        kr = self._keyring()
        if kr:
            try:
                v = kr.get_password(KEYRING_SERVICE, KEYRING_USER)
                if v:
                    return v
            except Exception:
                pass
        try:
            return json.loads(self._key_file().read_text()).get("anthropic_api_key", "")
        except (FileNotFoundError, json.JSONDecodeError):
            return ""

    def set_api_key(self, key: str) -> str:
        """Returns where it was stored: 'keychain' or 'file'."""
        kr = self._keyring()
        if kr:
            try:
                kr.set_password(KEYRING_SERVICE, KEYRING_USER, key)
                self._key_file().unlink(missing_ok=True)
                return "keychain"
            except Exception:
                pass
        f = self._key_file()
        f.write_text(json.dumps({"anthropic_api_key": key}))
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        return "file"

    def forget_api_key(self) -> None:
        kr = self._keyring()
        if kr:
            try:
                kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception:
                pass
        self._key_file().unlink(missing_ok=True)


def mask(key: str) -> str:
    return f"{key[:10]}…{key[-4:]}" if len(key) > 16 else ("set" if key else "")
