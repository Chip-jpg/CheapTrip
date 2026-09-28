from __future__ import annotations

import asyncio
from typing import List

import httpx

from ai_layer.formatter import (
    enhance_with_ai,
    format_digest,
    select_formatter,
)
from config import get_settings
from notifier.rate_limiter import RateLimiter
from storage.database import mark_alerted, was_route_alerted_recently
from storage.models import AlertTier, Trip
from utils.logging_config import get_logger
from utils.retry import async_retry

log = get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """
    Sends alerts to Telegram via Bot API.

    Enforces:
    - Rate limiting (max 5 instant/hour, 1 digest/day)
    - No duplicate alerts (via database check)
    - Graceful failure (log and continue)
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._rate_limiter = RateLimiter()  # sync init; call create() for DB-restored state
        self.enabled = bool(self._token and self._chat_id)

    @classmethod
    async def create(cls) -> "TelegramNotifier":
        """Async factory — creates a TelegramNotifier with a DB-restored rate limiter."""
        notifier = cls()
        notifier._rate_limiter = await RateLimiter.create()
        return notifier

    @async_retry(max_attempts=4, min_wait=2.0, max_wait=16.0)
    async def _send_raw(self, text: str, parse_mode: str = "Markdown") -> bool:
        if not self.enabled:
            log.warning("telegram_not_configured")
            return False

        if len(text) > 4096:
            log.warning("telegram_message_truncated", original_len=len(text))
            text = text[:4090] + "\n…"

        url = f"{_TELEGRAM_API}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            log.error(
                "telegram_send_failed",
                status=resp.status_code,
                body=resp.text[:200],
            )
            if resp.status_code == 429:
                # Telegram rate limit — wait and retry
                retry_after = resp.json().get("parameters", {}).get("retry_after", 30)
                await asyncio.sleep(retry_after)
                raise Exception(f"Telegram 429 — retry after {retry_after}s")
            return False

    async def send_instant_alert(self, trip: Trip) -> bool:
        if not self.enabled:
            log.info("telegram_disabled_dry_run", route=trip.route, cost=trip.total_cost_eur)
            return False

        if await was_route_alerted_recently(trip.route, within_hours=6.0):
            log.info("route_recently_alerted_suppressed", route=trip.route)
            return False

        if not await self._rate_limiter.try_send_instant():
            log.info("rate_limit_instant_skipped", route=trip.route)
            return False

        formatter = select_formatter(trip)
        base_msg = formatter(trip)
        message = await enhance_with_ai(trip, base_msg)

        success = await self._send_raw(message)
        if success:
            await mark_alerted(trip.hash, AlertTier.INSTANT)
            log.info("instant_alert_sent", route=trip.route, cost=trip.total_cost_eur)
        return success

    async def send_digest(self, trips: List[Trip]) -> bool:
        if not self.enabled:
            log.info("telegram_disabled_digest_dry_run", count=len(trips))
            return False

        if not await self._rate_limiter.try_send_digest():
            log.info("rate_limit_digest_skipped")
            return False

        if not trips:
            return True

        message = format_digest(trips)
        success = await self._send_raw(message)
        if success:
            for trip in trips:
                await mark_alerted(trip.hash, AlertTier.DIGEST)
            log.info("digest_sent", count=len(trips))
        return success

    async def send_system_message(self, text: str) -> bool:
        """Send a system/status message (startup, errors, etc.)"""
        return await self._send_raw(f"ℹ️ {text}", parse_mode="Markdown")

    async def process_instant_queue(self, trips: List[Trip]) -> int:
        """
        Send as many instant alerts as rate limit allows.
        Prioritizes by: booking_confidence (HIGH first), data_confidence desc, total_cost asc.
        Returns count of alerts sent.
        """
        _bc_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        sorted_trips = sorted(
            trips,
            key=lambda t: (
                _bc_order.get(t.booking_confidence.value, 2),
                -t.data_confidence_score,
                t.total_cost_eur,
            ),
        )

        sent = 0
        for trip in sorted_trips:
            remaining = await self._rate_limiter.instant_remaining()
            if remaining <= 0:
                log.info("instant_quota_exhausted", queued=len(sorted_trips) - sent)
                break
            success = await self.send_instant_alert(trip)
            if success:
                sent += 1
            # Brief pause between messages to avoid Telegram flood
            await asyncio.sleep(0.5)

        return sent
