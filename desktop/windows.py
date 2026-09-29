"""Windows specifics of the desktop app (B40); elsewhere these do nothing or fall back."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from utils.logging_config import get_logger

log = get_logger(__name__)


def set_app_id(app_id: str) -> None:
    """
    Group the window, the taskbar button and the notifications under one app
    (the Start menu shortcut has the same AppUserModelID, see installer.iss).
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError) as exc:
        log.warning("app_id_not_set", error=str(exc))


def allow_foreground() -> None:
    """
    Let another process (the running app) bring its window to the front: Windows
    only lets the program the user just started do that, so it passes the right on.
    """
    if sys.platform != "win32":
        return
    import ctypes

    ASFW_ANY = -1
    ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)


def message_box(title: str, text: str) -> None:
    """A plain message the user must see (the windowed app has no console)."""
    if sys.platform != "win32":
        print(f"{title}: {text}", file=sys.stderr)
        return
    import ctypes

    MB_OK_ICONINFORMATION = 0x40
    ctypes.windll.user32.MessageBoxW(None, text, title, MB_OK_ICONINFORMATION)


def open_path(target: str) -> None:
    """Open a folder in Explorer (or a settings page, e.g. "ms-settings:notifications"); elsewhere the file manager."""
    import subprocess

    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 (a folder or ms-settings: page the app chose, never user input)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "CheapTrip"  # the same value the installer's "start at sign-in" writes


def autostart_command() -> Optional[str]:
    """What Windows runs at sign-in: the installed app, in the tray; None outside the Windows app."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable)
    if exe.name.lower() != "cheaptrip.exe":  # cheaptrip-cli.exe: the app is next to it
        exe = exe.with_name("CheapTrip.exe")
    return f'"{exe}" --minimized'


def autostart_enabled() -> Optional[bool]:
    """Whether CheapTrip starts at sign-in (None: not something this copy can do)."""
    if autostart_command() is None:
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    command = autostart_command()
    if command is None:
        raise ValueError("starting at sign-in needs the installed Windows app")
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass


def system_prefers_dark() -> bool:
    """True when Windows is set to dark mode for apps (the app's "system" theme follows it)."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:  # an older Windows: light
        return False


def webview2_available() -> bool:
    """
    True when the app window can use Edge WebView2. pywebview would quietly
    fall back to Internet Explorer's engine, which can't run the app's screens.
    """
    if sys.platform != "win32":
        return False
    try:
        from webview.platforms import winforms  # the same check pywebview makes when it starts

        return bool(winforms.is_chromium)
    except Exception as exc:  # pythonnet or .NET missing
        log.warning("webview_unavailable", error=str(exc))
        return False
