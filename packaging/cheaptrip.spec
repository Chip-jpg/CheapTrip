# -*- mode: python -*-
"""
PyInstaller build of CheapTrip for Windows: a console app in one folder
(dist/cheaptrip/cheaptrip.exe plus _internal/), which packaging/installer.iss
wraps into a single setup .exe. packaging/build_windows.ps1 runs it from the
repository root:

    python -m PyInstaller --noconfirm --clean packaging/cheaptrip.spec
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is set by PyInstaller)

# Read-only files the app opens at run time (utils/paths.bundle_dir() finds them)
datas = [
    (str(ROOT / "utils" / "data" / "airports.csv"), "utils/data"),
    (str(ROOT / "config" / "user_preferences.yaml.example"), "config"),
    (str(ROOT / ".env.example"), "."),
]

a = Analysis(  # noqa: F821
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=[],
    excludes=["pytest", "respx", "tkinter"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cheaptrip",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="cheaptrip", upx=False)  # noqa: F821
