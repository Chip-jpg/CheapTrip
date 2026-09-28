from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
from typing import Deque

from config import get_settings
from utils.logging_config import get_logger

log = get_logger(__name__)


class RateLimiter:
    """
    Token-bucket style rate limiter for Telegram alerts.

    Hard limits per spec:
    - Instant alerts: max 5/hour
    - Digest: 1/day
    - No duplicate alerts ever (handled by dedup layer)

    State is persisted via alerts_sent DB table and restored on startup,
    so quotas survive engine restarts.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._instant_max = settings.instant_alerts_per_hour
        self._instant_sent: Deque[datetime] = deque()
        self._digest_sent: Deque[datetime] = deque()
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls) -> "RateLimiter":
        """Async factory — creates a RateLimiter pre-populated from DB history."""
        rl = cls()
        try:
            from storage.database import get_recent_alert_timestamps
            from storage.models import AlertTier

            instant_ts = await get_recent_alert_timestamps(AlertTier.INSTANT, 1.0)
            for ts in instant_ts:
                rl._instant_sent.append(ts)

            digest_ts = await get_recent_alert_timestamps(AlertTier.DIGEST, 24.0)
            for ts in digest_ts:
                rl._digest_sent.append(ts)

            log.info(
                "rate_limiter_restored",
                instant_in_last_hour=len(instant_ts),
                digest_in_last_day=len(digest_ts),
            )
        except Exception as exc:
            log.warning("rate_limiter_restore_failed", error=str(exc))
        return rl

    def _prune_old(self, queue: Deque[datetime], window: timedelta) -> None:
        cutoff = datetime.utcnow() - window
        while queue and queue[0] < cutoff:
            queue.popleft()

    async def try_send_instant(self) -> bool:
        """Atomically check quota and reserve a slot. Returns True if slot was acquired."""
        async with self._lock:
            self._prune_old(self._instant_sent, timedelta(hours=1))
            if len(self._instant_sent) < self._instant_max:
                self._instant_sent.append(datetime.utcnow())
                return True
            return False

    async def try_send_digest(self) -> bool:
        """Atomically check quota and reserve a digest slot. Returns True if slot was acquired."""
        async with self._lock:
            self._prune_old(self._digest_sent, timedelta(hours=24))
            if len(self._digest_sent) == 0:
                self._digest_sent.append(datetime.utcnow())
                return True
            return False

    async def release_instant(self) -> None:
        """Give back the most recent instant slot (the send it was reserved for failed)."""
        async with self._lock:
            if self._instant_sent:
                self._instant_sent.pop()

    async def release_digest(self) -> None:
        """Give back the digest slot (the digest send failed)."""
        async with self._lock:
            if self._digest_sent:
                self._digest_sent.pop()

    async def instant_remaining(self) -> int:
        async with self._lock:
            self._prune_old(self._instant_sent, timedelta(hours=1))
            return max(0, self._instant_max - len(self._instant_sent))
