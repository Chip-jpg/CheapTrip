"""
Windows notifications (B39), through desktop-notifier (WinRT toasts on
Windows, the desktop's notification service elsewhere).

What goes in a toast is built by the pure functions below (deal_toast,
digest_toast, ...), so it can be tested without a desktop. Buttons carry an
action: "open" opens a link; "details", "mute" and "show" are handed to
`on_action`, which shows the deal or screen, or mutes the destination (it may
return a coroutine: it runs on the event loop the toast was sent from).
Without an `on_action`, only the links are offered.

Windows shows the toasts as "CheapTrip": desktop-notifier registers the app
name and icon for APP_ID in the registry, and the installer's Start menu
shortcut uses the same AppUserModelID.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import webbrowser
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Set, Tuple

from config import get_settings
from storage.models import DealType, Trip
from utils import airports
from utils.links import stay_label
from utils.logging_config import get_logger

log = get_logger(__name__)

APP_ID = "CheapTrip"

Action = Tuple[str, str]  # ("open", url) | ("details", deal id) | ("mute", airport code) | ("show", screen)


@dataclass
class Toast:
    title: str
    message: str
    buttons: List[Tuple[str, Action]] = field(default_factory=list)
    click: Optional[Action] = None


# ── Content ───────────────────────────────────────────────────────────────────

def _euros(amount: float) -> str:
    return f"€{amount:,.0f}"


def _when(trip: Trip) -> str:
    """'Wed 21–28 Oct', 'Wed 21 Oct' for one-way, or the post's travel window."""
    dep, ret = trip.departure_date, trip.return_date
    if dep and ret and ret > dep:
        return f"{dep:%a} {stay_label(dep, ret)}"
    if dep:
        return f"{dep:%a} {dep.day} {dep:%b}"
    leg = trip.outbound_flight
    return (leg.travel_window if leg else None) or (trip.hotel.travel_window if trip.hotel else None) or ""


def _destination(trip: Trip) -> Tuple[Optional[str], str]:
    """(airport code or None, city name)."""
    if trip.outbound_flight:
        code = trip.outbound_flight.destination
        return code, airports.display_name(code)
    if trip.hotel:
        city = trip.hotel.location.split(",")[0].strip()
        return airports.iata_for_city(city), city
    return None, trip.route


def _how_cheap(trip: Trip) -> Optional[str]:
    if trip.previous_price_eur and trip.previous_price_eur > trip.total_cost_eur:
        return f"Now {_euros(trip.total_cost_eur)}, was {_euros(trip.previous_price_eur)}"
    if trip.discount_pct and trip.normal_price_eur:
        return f"{trip.discount_pct:.0f}% below the usual {_euros(trip.normal_price_eur)}"
    if trip.alert_reasons:
        reason = trip.alert_reasons[0]
        return reason[:1].upper() + reason[1:]
    return None


def deal_toast(trip: Trip) -> Toast:
    """An instant alert: what, how much, why, when; buttons Book / Details / Mute <city>."""
    kind = DealType(trip.deal_type)
    leg = trip.outbound_flight
    code, city = _destination(trip)
    price = _euros(trip.total_cost_eur)
    when = _when(trip)

    if kind == DealType.HOTEL_ONLY and trip.hotel:
        hotel = trip.hotel
        title = f"{hotel.name}, {city} · €{hotel.price_per_night_eur:.2f}/night"
        rating = f"{hotel.rating:g}/10" if hotel.rating else None
        parts = [rating, when]
    elif kind == DealType.PACKAGE and leg:
        title = f"{city} package · {price} per person"
        stay = f"Flight + {trip.nights} nights" if trip.nights else "Flight + hotel"
        parts = [f"{stay}, {leg.package_hotel}" if leg.package_hotel else stay, when]
    elif kind == DealType.REPOSITIONED and trip.repositioning_legs and leg:
        hub = airports.display_name(trip.repositioning_legs[0].hub)
        origin = airports.display_name(trip.repositioning_legs[0].origin)
        title = f"{origin} → {hub} → {city} · {price}"
        parts = [f"Via {hub}", when]
    elif kind == DealType.COMPLETE_TRIP and trip.hotel:
        title = f"{trip.route} · {price} with hotel"
        parts = [_how_cheap(trip), f"{trip.nights or trip.hotel.nights} nights at {trip.hotel.name}", when]
    else:
        title = f"{trip.route} · {price}"
        parts = [_how_cheap(trip), when, (leg.airline if leg else None) or (trip.source_list or [None])[0]]
    if trip.is_error_fare:
        title = f"⚠ Possible error fare: {title}"
        parts.insert(0, "Book fast, verify after")

    buttons: List[Tuple[str, Action]] = []
    book = (leg.booking_url if leg else None) or (trip.hotel.booking_url if trip.hotel else None)
    if book:
        buttons.append(("Book", ("open", book)))
    buttons.append(("Details", ("details", trip.hash)))
    if code:
        buttons.append((f"Mute {city}", ("mute", code)))
    return Toast(title, " · ".join(p for p in parts if p), buttons, click=("details", trip.hash))


def digest_toast(trips: Sequence[Trip]) -> Toast:
    best = sorted(trips, key=lambda t: t.total_cost_eur)
    named = [f"{_destination(t)[1]} {_euros(t.total_cost_eur)}" for t in best[:2]]
    more = f" and {len(best) - 2} more" if len(best) > 2 else ""
    return Toast("Today's best deals", ", ".join(named) + more, click=("show", "deals"))


def notice_toast(title: str, message: str) -> Toast:
    return Toast(title, message, click=("show", "activity"))


def test_toast() -> Toast:
    return Toast("CheapTrip notifications work", f"Deals will look like this. {date.today():%a %d %b}",
                 click=("show", "deals"))


# ── Sending ───────────────────────────────────────────────────────────────────

def _one_line(record: logging.LogRecord) -> bool:
    """desktop-notifier logs a whole traceback when the system refuses a notification: keep one line."""
    if record.exc_info and record.exc_info[1] is not None:
        record.msg, record.args = f"{record.getMessage()}: {record.exc_info[1]!r}", None
        record.exc_info = record.exc_text = None
    return True


class DesktopNotifier:
    """Shows toasts; never raises (a failure is logged and reported as False)."""

    def __init__(
        self,
        backend: Any = None,
        *,
        icon_path: Optional[Path] = None,
        open_url: Callable[[str], Any] = webbrowser.open,
        on_action: Optional[Callable[[str, str], Any]] = None,
    ) -> None:
        self._backend = backend
        self._icon_path = icon_path
        self._open_url = open_url
        self._on_action = on_action
        self._actions: Set[asyncio.Task] = set()
        self.failures = 0

    def _get_backend(self) -> Any:
        if self._backend is None:
            from desktop_notifier import DesktopNotifier as Backend
            from desktop_notifier import Icon

            icon = Icon(path=self._icon_path) if self._icon_path else None
            self._backend = Backend(app_name=APP_ID, app_icon=icon)
            for name in [n for n in logging.root.manager.loggerDict if n.startswith("desktop_notifier")]:
                logging.getLogger(name).addFilter(_one_line)  # filters don't apply to child loggers
        return self._backend

    def _handler(self, action: Action) -> Optional[Callable[[], None]]:
        kind, value = action
        if kind == "open":
            return lambda: self._open_url(value)
        if self._on_action is None:
            return None
        return lambda: self._act(kind, value)

    def _act(self, kind: str, value: str) -> None:
        """Run a button's action (desktop-notifier calls this on our event loop)."""
        try:
            result = self._on_action(kind, value)
            if inspect.isawaitable(result):
                task = asyncio.ensure_future(result)
                self._actions.add(task)
                task.add_done_callback(self._actions.discard)
        except Exception as exc:
            log.warning("desktop_notification_action_failed", action=kind, error=str(exc))

    async def show(self, toast: Toast) -> bool:
        try:
            from desktop_notifier import DEFAULT_SOUND, Button

            buttons = [Button(title=label, on_pressed=handler)
                       for label, action in toast.buttons if (handler := self._handler(action))]
            shown: List[bool] = []
            await self._get_backend().send(
                title=toast.title,
                message=toast.message,
                buttons=buttons,
                on_clicked=self._handler(toast.click) if toast.click else None,
                on_dispatched=lambda: shown.append(True),
                sound=DEFAULT_SOUND if get_settings().notify_sound else None,
            )
            if not shown:  # desktop-notifier logs a refused notification instead of raising
                raise RuntimeError("the system didn't accept the notification")
            return True
        except Exception as exc:  # no notification service (e.g. a server), or it failed
            self.failures += 1
            log.warning("desktop_notification_failed", error=str(exc), title=toast.title)
            return False

    async def send_deal(self, trip: Trip) -> bool:
        return await self.show(deal_toast(trip))

    async def send_digest(self, trips: Sequence[Trip]) -> bool:
        return await self.show(digest_toast(trips)) if trips else True

    async def send_notice(self, title: str, message: str) -> bool:
        return await self.show(notice_toast(title, message))

    async def send_test(self) -> bool:
        return await self.show(test_toast())
