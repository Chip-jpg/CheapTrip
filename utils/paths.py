"""
Where the app keeps its files.

A source checkout or a Docker container works from its current folder (.env,
config/, data/, logs/), as it always has. The installed Windows app lives in a
program folder that is replaced on every upgrade, so it keeps them in a per-user
home instead: %APPDATA%\\CheapTrip, or CHEAPTRIP_HOME when that is set.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

# Copied into a new home so there is something to edit (the installer writes its own)
_SEED_FILES = {
    Path("config") / "user_preferences.yaml": Path("config") / "user_preferences.yaml.example",
    Path(".env"): Path(".env.example"),
}


def is_frozen() -> bool:
    """True inside the packaged Windows app (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Read-only files shipped with the app: the PyInstaller bundle, or the source tree."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def app_home() -> Optional[Path]:
    """The per-user home, or None when the app works from its current folder."""
    override = os.getenv("CHEAPTRIP_HOME")
    if override:
        return Path(override)
    if is_frozen():
        return Path(os.getenv("APPDATA") or Path.home()) / "CheapTrip"
    return None


def enter_app_home() -> Optional[Path]:
    """
    Make the app home the working folder (so .env, data/ and logs/ live there),
    seed missing settings files from the bundled examples, and point the
    preferences at it. Returns the home, or None when there is none.
    """
    home = app_home()
    if home is None:
        return None
    home.mkdir(parents=True, exist_ok=True)
    os.chdir(home)
    for target, example in _SEED_FILES.items():
        source = bundle_dir() / example
        if not (home / target).exists() and source.exists():
            (home / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, home / target)
    os.environ.setdefault("CHEAPTRIP_PREFS_PATH", str(home / "config" / "user_preferences.yaml"))
    return home
