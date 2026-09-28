"""Tests for historical price analytics and anomaly detection."""
from __future__ import annotations

import pytest

from storage.price_analytics import AnomalyResult, PriceStats, _std_dev, detect_anomaly, get_price_stats


class TestComputeStdDev:
    def test_empty_returns_none(self):
        assert _std_dev([]) is None

    def test_single_value_returns_none(self):
        # Need at least 2 values for sample std_dev
        assert _std_dev([100.0]) is None

    def test_known_values(self):
        # mean=5.0, sample std_dev of [2,4,4,4,5,5,7,9] ≈ 2.14
        result = _std_dev([2, 4, 4, 4, 5, 5, 7, 9])
        assert result is not None
        assert abs(result - 2.14) < 0.1

    def test_identical_values(self):
        result = _std_dev([50.0, 50.0, 50.0])
        assert result == 0.0


class TestAnomalyResult:
    def test_no_anomaly_default(self):
        result = AnomalyResult(is_anomaly=False)
        assert result.is_anomaly is False
        assert result.is_all_time_low is False
        assert result.is_sudden_drop is False
        assert result.deviation_pct is None

    def test_anomaly_with_description(self):
        result = AnomalyResult(
            is_anomaly=True,
            is_all_time_low=True,
            deviation_pct=-45.0,
            description="45% below 30-day average",
        )
        assert result.is_anomaly is True
        assert result.description is not None


class TestGetPriceStats:
    @pytest.mark.asyncio
    async def test_returns_empty_stats_for_new_route(self):
        from storage.database import init_db
        await init_db()
        stats = await get_price_stats("TEST-ROUTE-XYZ-999")
        assert isinstance(stats, PriceStats)
        assert stats.sample_count_30d == 0
        assert stats.avg_30d is None
        assert stats.all_time_low is None


class TestDetectAnomaly:
    @pytest.mark.asyncio
    async def test_no_anomaly_for_new_route(self):
        from storage.database import init_db
        await init_db()
        result = await detect_anomaly("TEST-ROUTE-NEW-123", 200.0)
        assert isinstance(result, AnomalyResult)
        assert result.is_anomaly is False  # insufficient data

    @pytest.mark.asyncio
    async def test_returns_anomaly_result_type(self):
        from storage.database import init_db
        await init_db()
        result = await detect_anomaly("MXP-KRK", 50.0)
        assert hasattr(result, "is_anomaly")
        assert hasattr(result, "is_all_time_low")
        assert hasattr(result, "deviation_pct")


class TestSingleObservation:
    async def test_one_price_does_not_crash_stats(self, engine):
        # Regression: std dev of a single price is None and round(None) raised TypeError.
        from storage.database import init_db, record_price

        await init_db()
        await record_price("MXP-KRK", 25.0, "test")
        stats = await get_price_stats("MXP-KRK")
        assert stats.sample_count_30d == 1
        assert stats.std_dev_30d is None
        assert stats.avg_30d == 25.0
