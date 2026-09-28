"""
Historical price analytics and anomaly detection.

Computes statistics from the price_history table and detects:
- Prices below the historical average by ≥ N standard deviations
- New all-time lows
- Sudden drops vs. the most recent recorded price
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import List, Optional

import aiosqlite
from pydantic import BaseModel

from config import get_settings
from storage.database import get_db_path
from utils.logging_config import get_logger

log = get_logger(__name__)


class PriceStats(BaseModel):
    route: str
    avg_30d: Optional[float] = None
    avg_90d: Optional[float] = None
    all_time_low: Optional[float] = None
    std_dev_30d: Optional[float] = None
    sample_count_30d: int = 0
    last_price: Optional[float] = None
    last_recorded_at: Optional[datetime] = None


class AnomalyResult(BaseModel):
    is_anomaly: bool = False
    is_all_time_low: bool = False
    is_sudden_drop: bool = False
    is_below_avg: bool = False
    deviation_pct: Optional[float] = None  # negative = below avg
    description: Optional[str] = None


async def _fetch_prices(
    route: str,
    lookback_days: Optional[int] = None,
) -> List[float]:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        if lookback_days:
            cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
            cursor = await db.execute(
                "SELECT price_eur FROM price_history WHERE route = ? AND recorded_at >= ? ORDER BY price_eur",
                (route, cutoff),
            )
        else:
            cursor = await db.execute(
                "SELECT price_eur FROM price_history WHERE route = ? ORDER BY price_eur",
                (route,),
            )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def _fetch_last_price(route: str) -> Optional[float]:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT price_eur FROM price_history WHERE route = ? ORDER BY recorded_at DESC LIMIT 1",
            (route,),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


def _std_dev(prices: List[float]) -> Optional[float]:
    if len(prices) < 2:
        return None
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / (len(prices) - 1)
    return math.sqrt(variance)


def _mean(prices: List[float]) -> Optional[float]:
    return sum(prices) / len(prices) if prices else None


def _round_opt(value: Optional[float]) -> Optional[float]:
    return round(value, 2) if value is not None else None


async def get_price_stats(route: str) -> PriceStats:
    prices_30d = await _fetch_prices(route, 30)
    prices_90d = await _fetch_prices(route, 90)
    all_prices = await _fetch_prices(route)
    last_price = await _fetch_last_price(route)

    return PriceStats(
        route=route,
        avg_30d=_round_opt(_mean(prices_30d)),
        avg_90d=_round_opt(_mean(prices_90d)),
        all_time_low=_round_opt(min(all_prices)) if all_prices else None,
        std_dev_30d=_round_opt(_std_dev(prices_30d)),
        sample_count_30d=len(prices_30d),
        last_price=last_price,
    )


async def update_price_stats(route: str) -> None:
    """Recompute and upsert aggregated stats for a route into the price_stats table."""
    prices_30d = await _fetch_prices(route, 30)
    prices_90d = await _fetch_prices(route, 90)
    all_prices = await _fetch_prices(route)
    if not all_prices:
        return

    avg_30d = _mean(prices_30d)
    avg_90d = _mean(prices_90d)
    all_time_low = min(all_prices)
    std_dev_30d = _std_dev(prices_30d)
    now = datetime.utcnow().isoformat()

    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            INSERT INTO price_stats
                (route, all_time_low, avg_30d, avg_90d, std_dev_30d, sample_count, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(route) DO UPDATE SET
                all_time_low  = excluded.all_time_low,
                avg_30d       = excluded.avg_30d,
                avg_90d       = excluded.avg_90d,
                std_dev_30d   = excluded.std_dev_30d,
                sample_count  = excluded.sample_count,
                last_updated  = excluded.last_updated
            """,
            (route, all_time_low,
             round(avg_30d, 2) if avg_30d else None,
             round(avg_90d, 2) if avg_90d else None,
             round(std_dev_30d, 2) if std_dev_30d else None,
             len(all_prices), now),
        )
        await db.commit()


async def detect_anomaly(route: str, current_price: float) -> AnomalyResult:
    settings = get_settings()
    stats = await get_price_stats(route)

    if stats.sample_count_30d < 3:
        # Not enough history — can't declare an anomaly
        return AnomalyResult(is_anomaly=False)

    result = AnomalyResult()

    # Check all-time low
    if stats.all_time_low and current_price < stats.all_time_low * 1.02:
        result.is_all_time_low = True
        result.is_anomaly = True
        result.description = f"Matches or beats all-time low of €{stats.all_time_low:.0f}"

    # Check vs 30-day average
    if stats.avg_30d and stats.std_dev_30d:
        threshold = stats.avg_30d - settings.price_anomaly_std_dev_threshold * stats.std_dev_30d
        if current_price < threshold:
            result.is_below_avg = True
            result.is_anomaly = True
            deviation = (current_price - stats.avg_30d) / stats.avg_30d * 100
            result.deviation_pct = round(deviation, 1)
            desc = f"{abs(deviation):.0f}% below 30-day avg of €{stats.avg_30d:.0f}"
            result.description = (result.description + "; " + desc) if result.description else desc

    # Check sudden drop vs last recorded price
    if stats.last_price and stats.last_price > 0:
        drop_pct = (stats.last_price - current_price) / stats.last_price * 100
        if drop_pct >= settings.price_sudden_drop_pct:
            result.is_sudden_drop = True
            result.is_anomaly = True
            desc = f"Sudden drop of {drop_pct:.0f}% vs last recorded €{stats.last_price:.0f}"
            result.description = (result.description + "; " + desc) if result.description else desc

    log.debug(
        "anomaly_check",
        route=route,
        price=current_price,
        is_anomaly=result.is_anomaly,
        desc=result.description,
    )
    return result
