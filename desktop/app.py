"""
CheapTrip.exe: the desktop app (B40).

  CheapTrip.exe                  open the app (or show the running one)
  CheapTrip.exe --minimized      start in the tray (the sign-in shortcut)
  CheapTrip.exe cheaptrip://…    open a screen, e.g. cheaptrip://deal/<id>

The engine and the local API run in a background thread (engine_thread.py);
the window is a pywebview window on Edge WebView2 showing the app's screens
from the API. Closing the window keeps CheapTrip running in the tray (unless
CLOSE_TO_TRAY is off); Quit stops the engine cleanly. Without WebView2 the
screens open in the default browser instead.

`main.py app` runs the same from a source checkout.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Optional, Sequence

import control
from config import get_settings
from desktop.engine_thread import EngineThread
from desktop.instance import InstanceLock, hand_over, read_app_file, remove_app_file, write_app_file
from desktop.links import parse_args
from desktop.tray import Tray
from desktop.windows import allow_foreground, message_box, set_app_id, webview2_available
from notifier.desktop import APP_ID
from utils.logging_config import get_logger

log = get_logger(__name__)

WINDOW_SIZE = (1280, 800)
WINDOW_MIN_SIZE = (1100, 720)
START_SCREEN = "deals"


class DesktopApp:
    def __init__(self, folder: Path, *, minimized: bool = False, route: Optional[str] = None, port: int = 0) -> None:
        self.folder = folder
        self.minimized = minimized
        self.route = route
        self.window: Any = None
        self.server: Any = None
        self.tray = Tray(self)
        self.engine_thread = EngineThread(self._make_engine, shell=self, on_status=self.tray.update, port=port)
        self._done = threading.Event()
        self._quit_lock = threading.Lock()
        self._quitting = False

    def _make_engine(self) -> Any:
        from notifier.desktop import DesktopNotifier
        from scheduler.engine import Engine

        return Engine(desktop=DesktopNotifier(on_action=self.notification_action))

    # ── Called by the API, the tray and notifications (any thread) ────────────

    def show(self, route: Optional[str] = None) -> None:
        """Bring the window forward, on `route` if given."""
        if self.window is None:  # browser mode
            if self.server is not None:
                webbrowser.open(self.server.link(route if route is not None else START_SCREEN))
            return
        if route is not None:
            self.window.evaluate_js(f"window.location.hash = {json.dumps('#/' + route)}")
        self.window.show()
        self.window.restore()

    def quit(self) -> None:
        """Quit the app (returns at once; the engine stops in the background)."""
        threading.Thread(target=self.shutdown, name="cheaptrip-quit", daemon=True).start()

    def notification_action(self, kind: str, value: str) -> Any:
        """A notification's buttons (on the engine's loop): Details and Show open the app; Mute mutes."""
        if kind == "mute":
            return control.set_muted(value, True)
        self.show(f"deals/{value}" if kind == "details" else value)
        return None

    # ── Running ───────────────────────────────────────────────────────────────

    def run(self) -> int:
        self.server = self.engine_thread.start()
        write_app_file(self.folder, self.server.port, self.server.token)
        log.info("app_started", port=self.server.port, minimized=self.minimized)
        tray = self.tray.start()
        try:
            if webview2_available():
                self._run_window(hidden=self.minimized and tray)
            else:
                self._run_in_browser(tray)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
        return 0

    def _run_window(self, hidden: bool) -> None:
        import webview

        self.window = webview.create_window(
            "CheapTrip", self.server.link(self.route if self.route is not None else START_SCREEN),
            width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE, hidden=hidden,
            background_color="#FFFFFF",
        )
        self.window.events.closing += self._on_closing
        webview.start()  # returns when the window is destroyed

    def _on_closing(self) -> bool:
        """The window's close button: hide to the tray, or quit (False keeps the window)."""
        if self._quitting or not self.tray.available or not get_settings().close_to_tray:
            self._quitting = True  # run() shuts down once the window is gone
            return True
        self.window.hide()
        return False

    def _run_in_browser(self, tray: bool) -> None:
        if not self.minimized:  # started at sign-in: nothing to say until the user opens it
            self.show(self.route)
            if sys.platform == "win32":
                message_box("CheapTrip", "CheapTrip opened in your web browser: this PC doesn't have the Microsoft "
                                         "Edge WebView2 Runtime that its window needs. Installing it (free, from "
                                         "Microsoft) gives CheapTrip its own window.")
        if not tray:
            print(f"CheapTrip is running at {self.server.url}\nPress Ctrl+C to stop.", flush=True)
        self._done.wait()

    def shutdown(self) -> None:
        """Stop the tray, the engine and the window; safe to call more than once, from any thread."""
        with self._quit_lock:
            if self._done.is_set():
                return
            self._quitting = True
            log.info("app_quitting")
            self.tray.stop()
            self.engine_thread.stop()
            remove_app_file(self.folder)
            if self.window is not None:
                try:
                    self.window.destroy()
                except Exception:  # already closed
                    pass
            self._done.set()


def run(argv: Sequence[str], folder: Optional[Path] = None) -> int:
    """Start the app, or hand over to the running one. Logging and the working folder are set up already."""
    args = parse_args(argv)
    folder = folder or Path.cwd()
    set_app_id(APP_ID)
    lock = InstanceLock(folder)
    if not lock.acquire():
        if args.minimized:  # signed in again while CheapTrip runs: nothing to show
            return 0
        allow_foreground()  # the running app may bring its window to the front
        if hand_over(folder, args.route):
            return 0
        if read_app_file(folder) is None:
            message_box("CheapTrip", "CheapTrip is already running in a console window (cheaptrip-cli run). "
                                     "Close it, then open CheapTrip again.")
        else:
            message_box("CheapTrip", "CheapTrip is already running but doesn't answer. "
                                     "End it in Task Manager, then open CheapTrip again.")
        return 1
    try:
        return DesktopApp(folder, minimized=args.minimized, route=args.route).run()
    finally:
        lock.release()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CheapTrip.exe's entry point (packaging/app_entry.py)."""
    for name in ("stdout", "stderr"):  # a windowed app has no console: logs still go to the log file
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    from utils.logging_config import configure_logging
    from utils.paths import enter_app_home

    folder = enter_app_home() or Path.cwd()
    configure_logging()
    try:
        return run(sys.argv[1:] if argv is None else argv, folder)
    except Exception as exc:
        log.error("app_failed", error=str(exc), exc_info=True)
        message_box("CheapTrip", f"CheapTrip couldn't start: {exc}\n\nThe log is in {folder / 'logs'}.")
        return 1
