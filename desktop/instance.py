"""
One CheapTrip at a time (B40).

The engine must never run twice: two would search twice as often and send
every alert twice. The app, `cheaptrip-cli run` and `main.py ui` take the same
lock: a named mutex on Windows (per signed-in user), a lock file elsewhere.

A second launch of the app (a click on the Start menu entry, a cheaptrip://
link) hands over instead: it reads app.json, which the running app writes with
its API port and token, asks the running app to show its window, and exits.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from utils.logging_config import get_logger

log = get_logger(__name__)

MUTEX_NAME = "Local\\CheapTrip.Engine"
LOCK_FILE = Path("data") / "cheaptrip.lock"
APP_FILE = "app.json"
_ERROR_ALREADY_EXISTS = 183


class InstanceLock:
    """Held while an engine runs in this process; acquire() is False when another one holds it."""

    def __init__(self, folder: Path, name: str = MUTEX_NAME) -> None:
        self._folder = folder
        self._name = name
        self._handle: Any = None

    def acquire(self) -> bool:
        if sys.platform == "win32":
            return self._acquire_mutex()
        return self._acquire_file()

    def _acquire_mutex(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, self._name)
        if not handle:
            log.warning("instance_lock_unavailable", error=ctypes.get_last_error())
            return True  # never refuse to start because the lock itself failed
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def _acquire_file(self) -> bool:
        import fcntl

        path = self._folder / LOCK_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path, "a+")
        except OSError as exc:
            log.warning("instance_lock_unavailable", error=str(exc))
            return True
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        if sys.platform == "win32":
            import ctypes

            ctypes.WinDLL("kernel32").CloseHandle(self._handle)
        else:
            self._handle.close()
        self._handle = None


# ── Hand-over ─────────────────────────────────────────────────────────────────

def write_app_file(folder: Path, port: int, token: str) -> Path:
    """Where the running app listens, readable by this user only (%APPDATA% is private on Windows)."""
    path = folder / APP_FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"port": port, "token": token, "pid": os.getpid()}, handle)
    if sys.platform != "win32":
        os.chmod(path, 0o600)
    return path


def read_app_file(folder: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads((folder / APP_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data.get("port"), int) and data.get("token") else None
    except (OSError, ValueError, AttributeError):
        return None


def remove_app_file(folder: Path) -> None:
    """Remove app.json if this process wrote it."""
    data = read_app_file(folder)
    if data and data.get("pid") == os.getpid():
        (folder / APP_FILE).unlink(missing_ok=True)


def hand_over(folder: Path, route: Optional[str] = None, wait_s: float = 15.0) -> bool:
    """
    Ask the running app to show its window (on `route`); True when it did.
    Waits up to `wait_s` for an app that is still starting to write app.json.
    """
    deadline = time.monotonic() + wait_s
    while True:
        info = read_app_file(folder)
        if info is not None:
            try:
                # trust_env=False: a proxy from the environment must not see the token
                with httpx.Client(trust_env=False, timeout=10.0) as client:
                    resp = client.post(
                        f"http://127.0.0.1:{info['port']}/api/v1/app/show",
                        json={"route": route}, headers={"Authorization": f"Bearer {info['token']}"},
                    )
                if resp.status_code == 200:
                    return True
                log.warning("hand_over_refused", status=resp.status_code)
            except httpx.HTTPError as exc:
                log.warning("hand_over_failed", error=str(exc))
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)
