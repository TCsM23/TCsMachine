"""Entry point frozen into PALE.exe / PALE.app by PyInstaller."""
import sys

if sys.stdout is None or sys.stderr is None:  # windowed build: no console, so log to a file
    from pale.desktop.store import app_dir

    log = open(app_dir() / "pale.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log

from pale.desktop.app import main  # noqa: E402

main()
