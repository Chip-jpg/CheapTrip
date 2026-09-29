"""Windows specifics of the desktop app (B40); elsewhere these do nothing or fall back."""
from __future__ import annotations

import sys

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
