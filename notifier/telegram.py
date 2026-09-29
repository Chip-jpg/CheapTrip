from __future__ import annotations

import asyncio
from typing import Any, List, Optional, Tuple

import httpx

from ai_layer.formatter import (
    enhance_with_ai,
    format_digest,
    html_to_plain,
    select_formatter,
)
from config import get_settings
from notifier.rate_limiter import RateLimiter
from storage.database import mark_alerted, was_route_alerted_recently
from storage.models import AlertTier, BookingConfidence, Trip
from utils.events import get_event_bus
from utils.logging_config import get_logger

log = get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_MAX_MESSAGE_CHARS = 4096
_SEND_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 2.0
_MAX_RETRY_AFTER_S = 60.0


async def _pause(seconds: float) -> None:
    """Backoff sleep (patched to a no-op in tests)."""
    await asyncio.sleep(seconds)


def _truncate(text: str) -> str:
    """Cut an over-long message at a line boundary so HTML tags stay intact."""
    if len(text) <= _MAX_MESSAGE_CHARS:
        return text
    cut = text.rfind("\n", 0, _MAX_MESSAGE_CHARS - 2)
    log.warning("telegram_message_truncated", original_len=len(text))
    return text[: cut if cut > 0 else _MAX_MESSAGE_CHARS - 2] + "\n…"


class TelegramNotifier:
    """
    Sends alerts to Telegram via Bot API (HTML parse mode).

    Enforces:
    - Rate limiting (max N instant/hour, 1 digest/day); a failed send
      releases its slot
    - No duplicate alerts (via database check)
    - Graceful failure: sending never raises, it logs and returns False
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._rate_limiter = RateLimiter()  # sync init; call create() for DB-restored state
        self.enabled = bool(self._token and self._chat_id)

    @property
    def rate_limiter(self) -> RateLimiter:
        """The hourly/daily limits (shared with the desktop channel by notifier.channels)."""
        return self._rate_limiter

    async def compose_instant(self, trip: Trip) -> str:
        """The instant alert's Telegram HTML (formatter, then optional AI polish)."""
        return await enhance_with_ai(trip, select_formatter(trip)(trip))

    async def deliver(self, text: str) -> bool:
        """Send one Telegram HTML message; no limits or bookkeeping (see send_instant_alert)."""
        return await self._send_raw(text)

    @classmethod
    async def create(cls) -> "TelegramNotifier":
        """Async factory — creates a TelegramNotifier with a DB-restored rate limiter."""
        notifier = cls()
        notifier._rate_limiter = await RateLimiter.create()
        return notifier

    async def _send_raw(self, text: str, parse_mode: Optional[str] = "HTML") -> bool:
        """
        POST sendMessage. Retries network errors, 5xx and 429 (honouring
        retry_after); resends once as plain text if Telegram can't parse the
        HTML. Never raises.
        """
        if not self.enabled:
            log.warning("telegram_not_configured")
            return False

        url = f"{_TELEGRAM_API}/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": _truncate(text)}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        attempt = 0
        while attempt < _SEND_ATTEMPTS:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, json=payload)
            except httpx.HTTPError as exc:
                log.warning("telegram_send_error", attempt=attempt, error=str(exc))
                if attempt < _SEND_ATTEMPTS:
                    await _pause(_RETRY_BASE_DELAY_S * attempt)
                continue

            if resp.status_code == 200:
                return True

            body = resp.text[:300]
            if resp.status_code == 429:
                try:
                    retry_after = float(resp.json().get("parameters", {}).get("retry_after", 30))
                except ValueError:
                    retry_after = 30.0
                log.warning("telegram_rate_limited", attempt=attempt, retry_after=retry_after)
                if attempt < _SEND_ATTEMPTS:
                    await _pause(min(retry_after, _MAX_RETRY_AFTER_S))
                continue

            if resp.status_code >= 500:
                log.warning("telegram_server_error", attempt=attempt, status=resp.status_code, body=body)
                if attempt < _SEND_ATTEMPTS:
                    await _pause(_RETRY_BASE_DELAY_S * attempt)
                continue

            if resp.status_code == 400 and "parse" in body.lower() and "parse_mode" in payload:
                # Malformed markup: resend the same content as plain text (does not use up an attempt).
                log.warning("telegram_parse_error_plain_text_fallback", body=body)
                payload = {"chat_id": self._chat_id, "text": _truncate(html_to_plain(text))}
                attempt -= 1
                continue

            log.error("telegram_send_failed", status=resp.status_code, body=body)
            return False

        log.error("telegram_send_gave_up", attempts=_SEND_ATTEMPTS)
        return False

    async def send_instant_alert(self, trip: Trip) -> bool:
        if not self.enabled:
            log.info("telegram_disabled_dry_run", route=trip.route, cost=trip.total_cost_eur)
            return False

        # One alert per route every 6h, unless this is the same deal at a clearly lower price
        is_price_drop = trip.previous_price_eur is not None and trip.total_cost_eur < trip.previous_price_eur
        if not is_price_drop and await was_route_alerted_recently(trip.route, within_hours=6.0):
            log.info("route_recently_alerted_suppressed", route=trip.route)
            return False

        message = await self.compose_instant(trip)

        if not await self._rate_limiter.try_send_instant():
            log.info("rate_limit_instant_skipped", route=trip.route)
            return False

        success = await self._send_raw(message)
        if success:
            await mark_alerted(trip.hash, AlertTier.INSTANT)
            get_event_bus().publish("alert_sent", id=trip.hash, route=trip.route, price=trip.total_cost_eur,
                                    channels=["telegram"])
            log.info("instant_alert_sent", route=trip.route, cost=trip.total_cost_eur)
        else:
            await self._rate_limiter.release_instant()
        return success

    async def send_digest(self, trips: List[Trip]) -> bool:
        if not self.enabled:
            log.info("telegram_disabled_digest_dry_run", count=len(trips))
            return False

        if not trips:
            return True

        # Format before reserving the daily slot so a formatting error can't burn it.
        message = format_digest(trips)

        if not await self._rate_limiter.try_send_digest():
            log.info("rate_limit_digest_skipped")
            return False

        success = await self._send_raw(message)
        if success:
            for trip in trips:
                await mark_alerted(trip.hash, AlertTier.DIGEST)
            log.info("digest_sent", count=len(trips))
        else:
            await self._rate_limiter.release_digest()
        return success

    async def send_system_message(self, html_text: str, notice: Optional[Tuple[str, str]] = None) -> bool:
        """
        Send a system/status message (startup, cycle summary, health). Takes Telegram HTML.
        `notice` (title, text) is the plain version for a desktop notification (see notifier.channels).
        """
        return await self._send_raw(f"ℹ️ {html_text}")

    async def process_instant_queue(self, trips: List[Trip]) -> int:
        """Send as many instant alerts as the hourly limit allows, best deals first; returns how many."""
        return await run_instant_queue(self, trips)


def instant_priority(trip: Trip) -> tuple:
    """
    Sending order for instant alerts: biggest discount vs the route's usual
    price, then booking confidence (HIGH first), data confidence, lowest price.
    """
    bc_order = {BookingConfidence.HIGH: 0, BookingConfidence.MEDIUM: 1, BookingConfidence.LOW: 2}
    return (
        -(trip.discount_pct or 0.0),
        bc_order.get(BookingConfidence(trip.booking_confidence), 2),
        -trip.data_confidence_score,
        trip.total_cost_eur,
    )


async def run_instant_queue(notifier: Any, trips: List[Trip]) -> int:
    """Offer `trips` to notifier.send_instant_alert, best first, until its hourly limit is used up."""
    ordered = sorted(trips, key=instant_priority)
    sent = 0
    for trip in ordered:
        if await notifier.rate_limiter.instant_remaining() <= 0:
            log.info("instant_quota_exhausted", queued=len(ordered) - sent)
            break
        if await notifier.send_instant_alert(trip):
            sent += 1
            # Brief pause between messages to avoid Telegram flood limits
            await _pause(0.5)
    return sent
