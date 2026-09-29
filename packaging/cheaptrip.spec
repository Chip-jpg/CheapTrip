# -*- mode: python -*-
"""
PyInstaller build of CheapTrip for Windows: one folder (dist/cheaptrip/) with
two programs sharing _internal/, which packaging/installer.iss wraps into a
single setup .exe:

  CheapTrip.exe      the desktop app: window, tray, notifications (packaging/app_entry.py)
  cheaptrip-cli.exe  the console commands: doctor, cycle, run, ... (main.py)

(Windows file names ignore case, so the two can't both be called cheaptrip.)
packaging/build_windows.ps1 builds the UI first, then runs, from the repository root:

    python -m PyInstaller --noconfirm --clean packaging/cheaptrip.spec
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is set by PyInstaller)
UI = ROOT / "ui" / "dist"
if not (UI / "index.html").exists():
    raise SystemExit("The app's screens aren't built: run `npm ci` and `npm run build` in ui/ first")

# Read-only files the app opens at run time (utils/paths.bundle_dir() finds them)
datas = [
    (str(ROOT / "utils" / "data" / "airports.csv"), "utils/data"),
    (str(ROOT / "config" / "user_preferences.yaml.example"), "config"),
    (str(ROOT / ".env.example"), "."),
    (str(UI), "ui/dist"),
]
hiddenimports = []
if sys.platform == "win32":
    # Imported at run time by name: the tray's Windows backend, the notification backends
    # (WinRT) and pythonnet (the window's .NET side; pywebview's own hook adds WebView2's DLLs)
    hiddenimports += ["pystray._win32", "clr"]
    hiddenimports += collect_submodules("desktop_notifier.backends", filter=lambda name: "macos" not in name)
    hiddenimports += collect_submodules("winrt")
    datas += collect_data_files("pythonnet") + collect_data_files("clr_loader", include_py_files=False)

common = dict(pathex=[str(ROOT)], datas=datas, hiddenimports=hiddenimports, excludes=["pytest", "respx", "tkinter"])
cli = Analysis([str(ROOT / "main.py")], **common)  # noqa: F821
app = Analysis([str(ROOT / "packaging" / "app_entry.py")], **common)  # noqa: F821

cli_exe = EXE(  # noqa: F821
    PYZ(cli.pure), cli.scripts, [],  # noqa: F821
    exclude_binaries=True, name="cheaptrip-cli", console=True, upx=False,
)
app_exe = EXE(  # noqa: F821
    PYZ(app.pure), app.scripts, [],  # noqa: F821
    exclude_binaries=True, name="CheapTrip", console=False, upx=False,
)
coll = COLLECT(  # noqa: F821
    cli_exe, app_exe, cli.binaries, cli.datas, app.binaries, app.datas, name="cheaptrip", upx=False,
)
