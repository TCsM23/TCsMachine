# PyInstaller spec for the PALE desktop app.  Build:  pyinstaller packaging/pale.spec --noconfirm
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "pale", "desktop", "assets", "pale.ico" if sys.platform == "win32" else "pale.png")

hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("pale")
    + ["keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.fail"]
)

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "pale", "web", "static"), os.path.join("pale", "web", "static")),
        (os.path.join(ROOT, "pale", "desktop", "assets"), os.path.join("pale", "desktop", "assets")),
    ],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="PALE", console=False, icon=ICON,
    disable_windowed_traceback=False, upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PALE", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="PALE.app", icon=os.path.join(ROOT, "packaging", "pale-512.png"),
        bundle_identifier="io.github.tcsm23.pale",
        info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0"},
    )
