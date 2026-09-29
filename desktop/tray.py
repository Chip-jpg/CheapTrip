"""
The tray icon (B40): shows the engine's state and gives quick controls.

  icon     green running · blue searching · grey paused · amber needs attention · red stopped
  tooltip  "CheapTrip · next search 14:30", "… alerts paused until 08:00", …
  menu     Open CheapTrip · Search now · Pause alerts ▸ · Resume alerts · Quit CheapTrip

The menu is plain data (entries()), turned into a pystray menu only when the
tray starts, so its behaviour is tested without a desktop. The placeholder
icons are drawn here; the app's designed icons replace them in Round 8.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import control
from utils.logging_config import get_logger

log = get_logger(__name__)

STATE_COLORS = {
    "running": (34, 160, 90),
    "searching": (37, 99, 235),
    "paused": (130, 130, 140),
    "attention": (217, 119, 6),
    "stopped": (200, 40, 40),
}


def tray_image(state: str, size: int = 64) -> Any:
    """A round badge in the state's colour with a white paper plane."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((1, 1, size - 2, size - 2), fill=STATE_COLORS.get(state, STATE_COLORS["stopped"]) + (255,))
    s = size / 64
    draw.polygon([(16 * s, 33 * s), (48 * s, 18 * s), (38 * s, 48 * s), (31 * s, 38 * s)], fill=(255, 255, 255, 255))
    draw.polygon([(31 * s, 38 * s), (48 * s, 18 * s), (29 * s, 45 * s)], fill=(225, 225, 225, 255))
    return image


def _local_time(iso: str, tz_name: str) -> str:
    moment = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
    today = datetime.now(ZoneInfo(tz_name)).date()
    return f"{moment:%H:%M}" if moment.date() == today else f"{moment:%a %H:%M}"


def tooltip(status: Dict[str, Any]) -> str:
    state = status.get("state", "stopped")
    tz = status.get("timezone") or "UTC"
    if state == "searching":
        detail = "searching for deals…"
    elif state == "paused":
        until = status.get("paused_until")
        far = until and datetime.fromisoformat(until).year - datetime.now().year > 1
        detail = "alerts paused until you resume" if far or not until else \
            f"alerts paused until {_local_time(until, tz)}"
    elif state == "attention":
        detail = "needs attention: open it to see why"
    elif state == "stopped":
        detail = "stopped"
    else:
        nxt = status.get("next_search_at")
        detail = f"next search {_local_time(nxt, tz)}" if nxt else "running"
    return f"CheapTrip · {detail}"


@dataclass
class Entry:
    label: str
    action: Optional[Callable[[], Any]] = None
    submenu: List["Entry"] = field(default_factory=list)
    enabled: Callable[[], bool] = lambda: True
    visible: Callable[[], bool] = lambda: True
    default: bool = False  # what a click on the icon does


SEPARATOR = Entry("-")


class Tray:
    """`app` provides show(route), quit() and engine_thread (submit coroutines to the engine)."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.status: Dict[str, Any] = {"state": "stopped"}
        self._icon: Any = None

    @property
    def state(self) -> str:
        return self.status.get("state", "stopped")

    @property
    def available(self) -> bool:
        return self._icon is not None

    # ── Menu ──────────────────────────────────────────────────────────────────

    def entries(self) -> List[Entry]:
        not_paused = lambda: self.state != "paused"  # noqa: E731
        return [
            Entry("Open CheapTrip", self.open, default=True),
            Entry("Search now", self.search_now, enabled=lambda: self.state not in ("searching", "stopped")),
            Entry("Pause alerts", submenu=[
                Entry("For 1 hour", lambda: self.pause(hours=1)),
                Entry("For 4 hours", lambda: self.pause(hours=4)),
                Entry("Until tomorrow morning", lambda: self.pause(until="tomorrow")),
                Entry("Until I resume", lambda: self.pause(until="resume")),
            ], visible=not_paused),
            Entry("Resume alerts", self.resume, visible=lambda: self.state == "paused"),
            SEPARATOR,
            Entry("Quit CheapTrip", self.quit),
        ]

    def open(self) -> None:
        self.app.show(None)

    def search_now(self) -> None:
        self._run(self.app.engine_thread.engine.search_now())

    def pause(self, hours: Optional[float] = None, until: Optional[str] = None) -> None:
        self._run(self.app.engine_thread.engine.pause(**control.pause_end(hours, until)))

    def resume(self) -> None:
        self._run(self.app.engine_thread.engine.resume())

    def quit(self) -> None:
        self.app.quit()

    def _run(self, coro: Any) -> None:
        future = self.app.engine_thread.submit(coro)
        future.add_done_callback(
            lambda f: f.exception() and log.warning("tray_action_failed", error=str(f.exception())))

    # ── The icon ──────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Show the icon; False when there is no tray (a server, a desktop without one)."""
        try:
            import pystray

            self._icon = pystray.Icon("CheapTrip", tray_image(self.state), tooltip(self.status),
                                      menu=_to_pystray(pystray, self.entries()))
            if sys.platform == "win32":
                self._icon.run_detached()
            else:
                threading.Thread(target=self._icon.run, name="cheaptrip-tray", daemon=True).start()
            return True
        except Exception as exc:
            log.warning("tray_unavailable", error=str(exc))
            self._icon = None
            return False

    def update(self, status: Dict[str, Any]) -> None:
        """New engine status (from the engine thread)."""
        changed = status.get("state") != self.state
        self.status = status
        if self._icon is None:
            return
        try:
            if changed:
                self._icon.icon = tray_image(self.state)
            self._icon.title = tooltip(status)
            self._icon.update_menu()
        except Exception as exc:
            log.warning("tray_update_failed", error=str(exc))

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as exc:
                log.warning("tray_stop_failed", error=str(exc))
            self._icon = None


def _to_pystray(pystray: Any, entries: List[Entry]) -> Any:
    items = []
    for entry in entries:
        if entry is SEPARATOR:
            items.append(pystray.Menu.SEPARATOR)
            continue
        action = _to_pystray(pystray, entry.submenu) if entry.submenu else entry.action  # pystray adapts no-arg actions
        items.append(pystray.MenuItem(
            entry.label, action, default=entry.default,
            enabled=lambda item, e=entry: e.enabled(), visible=lambda item, e=entry: e.visible()))
    return pystray.Menu(*items)
