"""
Alerts on every channel (B39): Windows notifications and Telegram.

`ChannelNotifier` has the interface the runner and the engine use
(process_instant_queue, send_digest, send_system_message), and sends each
message to the channels switched on in Settings:

  NOTIFY_DESKTOP   desktop notifications (only where the app gave it a DesktopNotifier)
  NOTIFY_TELEGRAM  Telegram (only when a bot token and chat id are set)
  NOTIFY_INSTANT / NOTIFY_DIGEST  which kinds of alert go out at all

The hourly limit on instant alerts is shared: a deal shown on the desktop and
sent to Telegram uses one slot, and alerts_sent records both channels. A
channel that fails doesn't stop the other; the slot is given back only when
nothing reached you.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from ai_layer.formatter import format_digest
from config import get_settings
from notifier.desktop import DesktopNotifier
from notifier.rate_limiter import RateLimiter
from notifier.telegram import TelegramNotifier, run_instant_queue
from storage.database import mark_alerted, was_route_alerted_recently
from storage.models import AlertTier, Trip
from utils.events import get_event_bus
from utils.logging_config import get_logger

log = get_logger(__name__)

DESKTOP, TELEGRAM = "desktop", "telegram"


class ChannelNotifier:
    def __init__(self, telegram: TelegramNotifier, desktop: Optional[DesktopNotifier] = None) -> None:
        self.telegram = telegram
        self.desktop = desktop

    @classmethod
    async def create(cls, desktop: Optional[DesktopNotifier] = None) -> "ChannelNotifier":
        return cls(await TelegramNotifier.create(), desktop)

    async def reload_telegram(self) -> None:
        """Pick up a new bot token, chat id or hourly limit (Settings)."""
        self.telegram = await TelegramNotifier.create()

    @property
    def rate_limiter(self) -> RateLimiter:
        return self.telegram.rate_limiter

    def channels(self, kind: str = "instant") -> List[str]:
        """The channels a message of this kind ("instant", "digest", "system") goes to now."""
        settings = get_settings()
        if (kind == "instant" and not settings.notify_instant) or (kind == "digest" and not settings.notify_digest):
            return []
        found = []
        if self.desktop is not None and settings.notify_desktop and kind != "system":
            found.append(DESKTOP)
        if self.telegram.enabled and settings.notify_telegram:
            found.append(TELEGRAM)
        return found

    # ── Instant alerts ────────────────────────────────────────────────────────

    async def send_instant_alert(self, trip: Trip) -> bool:
        channels = self.channels("instant")
        if not channels:
            log.info("notifications_off_dry_run", route=trip.route, cost=trip.total_cost_eur)
            return False

        # One alert per route every 6h, unless this is the same deal at a clearly lower price
        is_price_drop = trip.previous_price_eur is not None and trip.total_cost_eur < trip.previous_price_eur
        if not is_price_drop and await was_route_alerted_recently(trip.route, within_hours=6.0):
            log.info("route_recently_alerted_suppressed", route=trip.route)
            return False

        # Compose before reserving a slot, so a formatting error can't use one up
        text = await self.telegram.compose_instant(trip) if TELEGRAM in channels else ""
        if not await self.rate_limiter.try_send_instant():
            log.info("rate_limit_instant_skipped", route=trip.route)
            return False

        sent = []
        if DESKTOP in channels and await self.desktop.send_deal(trip):
            sent.append(DESKTOP)
        if TELEGRAM in channels and await self.telegram.deliver(text):
            sent.append(TELEGRAM)

        if not sent:
            await self.rate_limiter.release_instant()
            return False
        await mark_alerted(trip.hash, AlertTier.INSTANT, channel=",".join(sent))
        get_event_bus().publish("alert_sent", id=trip.hash, route=trip.route, price=trip.total_cost_eur,
                                channels=sent)
        log.info("instant_alert_sent", route=trip.route, cost=trip.total_cost_eur, channels=sent)
        return True

    async def process_instant_queue(self, trips: List[Trip]) -> int:
        """Send as many instant alerts as the hourly limit allows, best deals first; returns how many."""
        return await run_instant_queue(self, trips)

    # ── Daily digest ──────────────────────────────────────────────────────────

    async def send_digest(self, trips: Sequence[Trip]) -> bool:
        channels = self.channels("digest")
        if not channels:
            log.info("notifications_off_digest_dry_run", count=len(trips))
            return False
        if not trips:
            return True

        text = format_digest(list(trips)) if TELEGRAM in channels else ""
        if not await self.rate_limiter.try_send_digest():
            log.info("rate_limit_digest_skipped")
            return False

        sent = []
        if DESKTOP in channels and await self.desktop.send_digest(trips):
            sent.append(DESKTOP)
        if TELEGRAM in channels and await self.telegram.deliver(text):
            sent.append(TELEGRAM)

        if not sent:
            await self.rate_limiter.release_digest()
            return False
        for trip in trips:
            await mark_alerted(trip.hash, AlertTier.DIGEST, channel=",".join(sent))
        get_event_bus().publish("digest_sent", count=len(trips), channels=sent)
        log.info("digest_sent", count=len(trips), channels=sent)
        return True

    # ── Status messages ───────────────────────────────────────────────────────

    async def send_system_message(self, html_text: str, notice: Optional[Tuple[str, str]] = None) -> bool:
        """
        Telegram gets every status message (startup, cycle summary, sources);
        the desktop only those with a `notice` (title, text): a source that
        fails or recovers.
        """
        sent = False
        if notice and self.desktop is not None and get_settings().notify_desktop:
            sent = await self.desktop.send_notice(*notice)
        if TELEGRAM in self.channels("system"):
            sent = await self.telegram.send_system_message(html_text) or sent
        return sent

    async def send_test(self, only: Optional[str] = None) -> Dict[str, bool]:
        """A test message on each channel that is on (or just `only`); {channel: delivered}."""
        results: Dict[str, bool] = {}
        for channel in self.channels("test"):
            if only and channel != only:
                continue
            if channel == DESKTOP:
                results[DESKTOP] = await self.desktop.send_test()
            else:
                results[TELEGRAM] = await self.telegram.deliver(
                    "✅ <b>CheapTrip notifications work.</b> Deals will arrive here.")
        return results
