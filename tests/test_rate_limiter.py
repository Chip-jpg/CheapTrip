"""Tests for the atomic rate limiter."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from notifier.rate_limiter import RateLimiter
from utils.timeutil import utcnow


@pytest.fixture
def limiter() -> RateLimiter:
    return RateLimiter()


class TestInstantSlots:
    def test_first_slot_is_granted(self, limiter):
        result = asyncio.run(limiter.try_send_instant())
        assert result is True

    def test_slot_is_recorded_atomically(self, limiter):
        asyncio.run(limiter.try_send_instant())
        remaining = asyncio.run(limiter.instant_remaining())
        # default max is 5; after 1 send, 4 remain
        assert remaining == limiter._instant_max - 1

    def test_quota_exhausted_returns_false(self, limiter):
        for _ in range(limiter._instant_max):
            asyncio.run(limiter.try_send_instant())
        result = asyncio.run(limiter.try_send_instant())
        assert result is False

    def test_remaining_at_zero_when_full(self, limiter):
        for _ in range(limiter._instant_max):
            asyncio.run(limiter.try_send_instant())
        assert asyncio.run(limiter.instant_remaining()) == 0

    def test_old_entries_pruned_from_window(self, limiter):
        # Backfill timestamps that are just over 1 hour old
        old = utcnow() - timedelta(hours=1, minutes=1)
        for _ in range(limiter._instant_max):
            limiter._instant_sent.append(old)
        # All old entries pruned → slot should be available again
        result = asyncio.run(limiter.try_send_instant())
        assert result is True


class TestDigestSlots:
    def test_first_digest_is_granted(self, limiter):
        result = asyncio.run(limiter.try_send_digest())
        assert result is True

    def test_second_digest_within_24h_rejected(self, limiter):
        asyncio.run(limiter.try_send_digest())
        result = asyncio.run(limiter.try_send_digest())
        assert result is False

    def test_old_digest_entry_pruned(self, limiter):
        old = utcnow() - timedelta(hours=25)
        limiter._digest_sent.append(old)
        result = asyncio.run(limiter.try_send_digest())
        assert result is True


class TestConcurrentAtomicity:
    def test_concurrent_requests_dont_exceed_quota(self):
        """Two concurrent coroutines racing to claim the last slot should only get one."""
        limiter = RateLimiter()
        # Fill to max - 1
        for _ in range(limiter._instant_max - 1):
            asyncio.run(limiter.try_send_instant())

        async def _race():
            results = await asyncio.gather(
                limiter.try_send_instant(),
                limiter.try_send_instant(),
            )
            return results

        results = asyncio.run(_race())
        # Exactly one should succeed (the lock ensures only one gets the last slot)
        assert sum(results) == 1
        assert asyncio.run(limiter.instant_remaining()) == 0
