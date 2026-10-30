"""PALE desktop app: a native window around the local web terminal.

    PALE.exe -> single-instance check -> load API key from the OS keychain -> open the active workspace
             -> start the server on a random localhost port -> native window (pywebview / WebView2)
             -> background update checks against GitHub Releases

If pywebview can't start (no WebView2 runtime, headless Linux), the app falls back to opening the
same UI in the default browser.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx

from .. import __version__, config
from ..runtime import init_project
from .store import Store, app_dir, default_workspace
from .updater import Updater

ASSETS = Path(__file__).parent / "assets"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Desktop:
    """What the server needs from the desktop shell."""

    def __init__(self, store: Store):
        self.store = store
        self.window = None
        self.updater = Updater(lambda: store.update_repo, store.folder, lambda: store.data["update"]["skipped"],
                               quit_fn=self.quit)
        self.default_workspace = default_workspace

    def pick_folder(self) -> str | None:
        if not self.window:
            return None
        import webview

        res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else None

    def open_path(self, p: Path) -> None:
        p.mkdir(parents=True, exist_ok=True) if not p.exists() else None
        if sys.platform == "win32":
            os.startfile(p)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])

    def focus(self) -> None:
        if self.window:
            try:
                self.window.restore()
                self.window.show()
            except Exception:
                pass

    def quit(self) -> None:
        if self.window:
            try:
                self.window.destroy()
                return
            except Exception:
                pass
        os._exit(0)


# ------------------------------------------------------------------ single instance

def _lock_file() -> Path:
    return app_dir() / "instance.json"


def other_instance() -> dict | None:
    try:
        info = json.loads(_lock_file().read_text())
        r = httpx.post(f"http://127.0.0.1:{info['port']}/api/desktop/focus",
                       headers={"X-Pale-Token": info["token"]}, timeout=1.5)
        return info if r.status_code == 200 else None
    except Exception:
        return None


def main() -> None:
    if other_instance():
        return  # the running window was brought to the front

    store = Store()
    key = os.environ.get("ANTHROPIC_API_KEY") or store.get_api_key()
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key

    active = Path(store.data["active"]) if store.data["active"] else default_workspace()
    init_project(active)
    if not store.data["workspaces"]:
        store.add_workspace(active, "sector-01")
        store.data["active"] = str(active.resolve())
        store.save()
    settings = config.load(active)

    import uvicorn

    from ..web.server import create_app

    desktop = Desktop(store)
    app = create_app(settings, desktop=desktop, scheduler=True)

    from fastapi import Header, HTTPException

    @app.post("/api/desktop/focus")
    def focus(x_pale_token: str = Header("")):
        if x_pale_token != app.state.token:
            raise HTTPException(401)
        desktop.focus()
        return {"ok": True}

    port = free_port()
    _lock_file().write_text(json.dumps({"pid": os.getpid(), "port": port, "token": app.state.token}))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    threading.Thread(target=server.run, daemon=True, name="pale-server").start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    desktop.updater.start_background(6, lambda: store.data["update"]["auto_check"])
    url = f"http://127.0.0.1:{port}/?token={app.state.token}"

    try:
        import webview

        icon = ASSETS / ("pale.ico" if sys.platform == "win32" else "pale.png")
        desktop.window = webview.create_window(
            f"PALE // Unit Terminal  v{__version__}", url, width=1440, height=900, min_size=(980, 640),
            background_color="#d6d0b8", text_select=True,
        )
        webview.start(icon=str(icon) if icon.exists() else None, private_mode=False,
                      storage_path=str(app_dir() / "webview"))
    except Exception as e:  # no native webview available: use the browser
        print(f"native window unavailable ({e}); opening the browser instead")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    finally:
        server.should_exit = True
        try:
            _lock_file().unlink()
        except OSError:
            pass
        from .. import mcp

        mcp.shutdown()
        os._exit(0)


if __name__ == "__main__":
    main()
