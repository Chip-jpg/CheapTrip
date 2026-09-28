from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import List, Optional

import aiosqlite

from config import get_settings
from storage.models import AlertTier, Trip
from utils.logging_config import get_logger

log = get_logger(__name__)


_CREATE_DEALS_TABLE = """
CREATE TABLE IF NOT EXISTS deals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash        TEXT UNIQUE NOT NULL,
    trip_id     TEXT NOT NULL,
    route       TEXT NOT NULL,
    deal_type   TEXT NOT NULL,
    total_cost  REAL NOT NULL,
    discount_pct REAL,
    confidence  REAL NOT NULL,
    alert_tier  TEXT NOT NULL,
    is_alerted  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    payload     TEXT NOT NULL
)
"""

_CREATE_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS alerts_sent (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hash       TEXT NOT NULL,
    alert_tier TEXT NOT NULL,
    sent_at    TEXT NOT NULL
)
"""

_CREATE_PRICES_TABLE = """
CREATE TABLE IF NOT EXISTS price_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    route          TEXT NOT NULL,
    price_eur      REAL NOT NULL,
    source         TEXT NOT NULL,
    recorded_at    TEXT NOT NULL
)
"""

_CREATE_PRICE_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS price_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    route        TEXT NOT NULL,
    all_time_low REAL,
    avg_30d      REAL,
    avg_90d      REAL,
    std_dev_30d  REAL,
    sample_count INTEGER DEFAULT 0,
    last_updated TEXT NOT NULL,
    UNIQUE(route)
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_deals_hash ON deals(hash)",
    "CREATE INDEX IF NOT EXISTS idx_deals_created ON deals(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_deals_alerted ON deals(is_alerted)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_sent_at ON alerts_sent(sent_at)",
    "CREATE INDEX IF NOT EXISTS idx_prices_route ON price_history(route)",
    "CREATE INDEX IF NOT EXISTS idx_price_stats_route ON price_stats(route)",
]


async def get_db_path() -> str:
    settings = get_settings()
    # Strip SQLAlchemy prefix for raw aiosqlite usage
    url = settings.database_url
    if url.startswith("sqlite+aiosqlite:///"):
        path = url[len("sqlite+aiosqlite:///"):]
    elif url.startswith("sqlite:///"):
        path = url[len("sqlite:///"):]
    else:
        path = "./data/travel_deals.db"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path


async def init_db() -> None:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.execute(_CREATE_DEALS_TABLE)
        await db.execute(_CREATE_ALERTS_TABLE)
        await db.execute(_CREATE_PRICES_TABLE)
        await db.execute(_CREATE_PRICE_STATS_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await db.execute(idx_sql)
        await db.commit()


async def save_deal(trip: Trip) -> bool:
    """Insert a deal; returns True if new, False if duplicate hash."""
    path = await get_db_path()
    expires = datetime.utcnow() + timedelta(days=7)
    async with aiosqlite.connect(path) as db:
        try:
            await db.execute(
                """
                INSERT INTO deals
                    (hash, trip_id, route, deal_type, total_cost, discount_pct,
                     confidence, alert_tier, is_alerted, created_at, expires_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    trip.hash,
                    trip.trip_id,
                    trip.route,
                    trip.deal_type,
                    trip.total_cost_eur,
                    trip.discount_pct,
                    trip.data_confidence_score,
                    trip.alert_tier,
                    trip.created_at.isoformat(),
                    expires.isoformat(),
                    trip.model_dump_json(),
                ),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def is_duplicate(trip_hash: str) -> bool:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM deals WHERE hash = ? LIMIT 1", (trip_hash,)
        )
        row = await cursor.fetchone()
        return row is not None


async def mark_alerted(trip_hash: str, tier: AlertTier) -> None:
    path = await get_db_path()
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE deals SET is_alerted = 1 WHERE hash = ?", (trip_hash,)
        )
        await db.execute(
            "INSERT INTO alerts_sent (hash, alert_tier, sent_at) VALUES (?, ?, ?)",
            (trip_hash, tier, now),
        )
        await db.commit()


async def count_alerts_sent_last_hour(tier: AlertTier) -> int:
    path = await get_db_path()
    cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM alerts_sent WHERE alert_tier = ? AND sent_at >= ?",
            (tier, cutoff),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_pending_instant_alerts(limit: int = 10) -> List[Trip]:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT payload FROM deals
            WHERE alert_tier = ? AND is_alerted = 0
            ORDER BY confidence DESC, total_cost ASC
            LIMIT ?
            """,
            (AlertTier.INSTANT, limit),
        )
        rows = await cursor.fetchall()
    trips = []
    for row in rows:
        try:
            trips.append(Trip.model_validate_json(row[0]))
        except Exception:
            log.warning("corrupted_trip_payload_skipped", preview=row[0][:20] if row[0] else "?")
    return trips


async def get_digest_deals(limit: int = 20) -> List[Trip]:
    path = await get_db_path()
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT payload FROM deals
            WHERE alert_tier IN (?, ?)
              AND created_at >= ?
            ORDER BY confidence DESC, total_cost ASC
            LIMIT ?
            """,
            (AlertTier.INSTANT, AlertTier.DIGEST, cutoff, limit),
        )
        rows = await cursor.fetchall()
    trips = []
    for row in rows:
        try:
            trips.append(Trip.model_validate_json(row[0]))
        except Exception:
            log.warning("corrupted_trip_payload_skipped", preview=row[0][:20] if row[0] else "?")
    return trips


async def record_price(route: str, price_eur: float, source: str) -> None:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO price_history (route, price_eur, source, recorded_at) VALUES (?, ?, ?, ?)",
            (route, price_eur, source, datetime.utcnow().isoformat()),
        )
        await db.commit()


async def get_recent_alert_timestamps(tier: AlertTier, within_hours: float) -> list:
    """Return UTC datetimes of alerts sent for this tier within the given window."""
    path = await get_db_path()
    cutoff = (datetime.utcnow() - timedelta(hours=within_hours)).isoformat()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT sent_at FROM alerts_sent WHERE alert_tier = ? AND sent_at >= ?",
            (tier, cutoff),
        )
        rows = await cursor.fetchall()
    return [datetime.fromisoformat(r[0]) for r in rows]


async def was_route_alerted_recently(route: str, within_hours: float = 6.0) -> bool:
    """True if any deal on this exact route was alerted within the window."""
    path = await get_db_path()
    cutoff = (datetime.utcnow() - timedelta(hours=within_hours)).isoformat()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT 1 FROM alerts_sent a
            JOIN deals d ON a.hash = d.hash
            WHERE d.route = ? AND a.sent_at >= ?
            LIMIT 1
            """,
            (route, cutoff),
        )
        row = await cursor.fetchone()
    return row is not None


async def get_price_median(route: str, lookback_days: int = 30) -> Optional[float]:
    path = await get_db_path()
    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT price_eur FROM price_history
            WHERE route = ? AND recorded_at >= ?
            ORDER BY price_eur
            """,
            (route, cutoff),
        )
        rows = await cursor.fetchall()
    if not rows:
        return None
    prices = [r[0] for r in rows]
    mid = len(prices) // 2
    if len(prices) % 2 == 0:
        return (prices[mid - 1] + prices[mid]) / 2
    return prices[mid]
