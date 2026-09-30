"""
Keeps the window drawing when one of Edge WebView2's processes fails (B47).

WebView2 draws the page in its own renderer and GPU processes. When one of them
dies, pywebview does nothing about it and the window shows only its background:
a blank window, with nothing in the log. This watches for WebView2's
ProcessFailed event, logs what failed, and reloads the page when the page itself
was lost (at most once a minute, so a page that keeps failing can't loop).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from utils.logging_config import get_logger

log = get_logger(__name__)

RELOAD_AT_MOST_EVERY_S = 60.0
# The failures that lose the page (the renderer); WebView2 restarts the GPU process by itself,
# and a lost browser process takes the whole WebView2 with it (the app must be restarted)
PAGE_LOST = ("RenderProcessExited", "RenderProcessUnresponsive", "FrameRenderProcessExited")


def _on_ui_thread(form: Any, action: Callable[[], None]) -> None:
    """Run `action` on the window's own thread (WebView2 may only be touched there)."""
    if getattr(form, "InvokeRequired", False):
        from System import Func, Type  # pythonnet, on Windows with WebView2 only

        form.Invoke(Func[Type](action))
    else:
        action()


class ProcessWatch:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_reload: Optional[float] = None
        self.attached = False

    def attach(self, window: Any) -> bool:
        """Watch a pywebview window's WebView2; False when it has none (not Windows, or not WebView2)."""
        if self.attached:
            return True
        form = getattr(window, "native", None)
        webview = getattr(getattr(form, "browser", None), "webview", None)
        if webview is None:
            return False

        def subscribe() -> None:
            core = webview.CoreWebView2
            if core is None:  # not initialised yet: the next page load tries again
                return
            core.ProcessFailed += self.on_failed
            self.attached = True

        try:
            _on_ui_thread(form, subscribe)
        except Exception as exc:  # an older WebView2 or pywebview: the app works without the watch
            log.warning("webview_watch_unavailable", error=str(exc))
            return False
        if self.attached:
            log.info("webview_watch_attached")
        return self.attached

    def on_failed(self, sender: Any, args: Any) -> None:
        """WebView2's ProcessFailed (on the window's thread): log it, and reload a lost page."""
        kind = str(getattr(args, "ProcessFailedKind", "unknown"))
        log.error("webview_process_failed", kind=kind, reason=str(getattr(args, "Reason", "")),
                  exit_code=getattr(args, "ExitCode", None), description=str(getattr(args, "ProcessDescription", "")))
        if not any(lost in kind for lost in PAGE_LOST):
            return
        now = self._clock()
        if self._last_reload is not None and now - self._last_reload < RELOAD_AT_MOST_EVERY_S:
            log.warning("webview_reload_skipped", kind=kind, reason="reloaded less than a minute ago")
            return
        self._last_reload = now
        try:
            sender.Reload()
            log.info("webview_reloaded", kind=kind)
        except Exception as exc:
            log.error("webview_reload_failed", kind=kind, error=str(exc))
