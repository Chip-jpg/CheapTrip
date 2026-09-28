from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Dict, List, Sequence, Tuple

import aiosqlite

from config import get_settings
from storage.models import AlertTier, DealType, Trip
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
    recorded_at    TEXT NOT NULL,
    origin_city    TEXT,
    dest_city      TEXT,
    trip_type      TEXT,
    nights_bucket  TEXT,
    depart_month   TEXT,
    cycle_id       TEXT
)
"""

# Columns added after the first release: added to existing databases by init_db
_PRICE_HISTORY_KEY_COLUMNS = ("origin_city", "dest_city", "trip_type", "nights_bucket", "depart_month", "cycle_id")

_CREATE_SCRAPER_HEALTH_TABLE = """
CREATE TABLE IF NOT EXISTS scraper_health (
    source_id            TEXT PRIMARY KEY,
    last_run_at          TEXT,
    last_success_at      TEXT,
    last_status          TEXT,
    last_count           INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_empty    INTEGER NOT NULL DEFAULT 0,
    total_runs           INTEGER NOT NULL DEFAULT 0,
    total_successes      INTEGER NOT NULL DEFAULT 0,
    notified_status      TEXT
)
"""

_CREATE_SEARCH_CURSOR_TABLE = """
CREATE TABLE IF NOT EXISTS search_cursor (
    cursor_key  TEXT PRIMARY KEY,
    position    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_deals_hash ON deals(hash)",
    "CREATE INDEX IF NOT EXISTS idx_deals_created ON deals(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_deals_alerted ON deals(is_alerted)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_sent_at ON alerts_sent(sent_at)",
    "CREATE INDEX IF NOT EXISTS idx_prices_route ON price_history(route)",
    "CREATE INDEX IF NOT EXISTS idx_prices_recorded ON price_history(recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_deals_expires ON deals(expires_at)",
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


async def _add_missing_columns(db: aiosqlite.Connection, table: str, columns: Sequence[str]) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in await cursor.fetchall()}
    for column in columns:
        if column not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


async def init_db() -> None:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.execute(_CREATE_DEALS_TABLE)
        await db.execute(_CREATE_ALERTS_TABLE)
        await db.execute(_CREATE_PRICES_TABLE)
        await _add_missing_columns(db, "price_history", _PRICE_HISTORY_KEY_COLUMNS)
        await db.execute(_CREATE_SCRAPER_HEALTH_TABLE)
        await db.execute(_CREATE_SEARCH_CURSOR_TABLE)
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
                    DealType(trip.deal_type).value,
                    trip.total_cost_eur,
                    trip.discount_pct,
                    trip.data_confidence_score,
                    AlertTier(trip.alert_tier).value,
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
            (trip_hash, AlertTier(tier).value, now),
        )
        await db.commit()


async def count_alerts_sent_last_hour(tier: AlertTier) -> int:
    path = await get_db_path()
    cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM alerts_sent WHERE alert_tier = ? AND sent_at >= ?",
            (AlertTier(tier).value, cutoff),
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
            ORDER BY COALESCE(discount_pct, 0) DESC, confidence DESC, total_cost ASC
            LIMIT ?
            """,
            (AlertTier.INSTANT.value, limit),
        )
        rows = await cursor.fetchall()
    trips = []
    for row in rows:
        try:
            trips.append(Trip.model_validate_json(row[0]))
        except Exception:
            log.warning("corrupted_trip_payload_skipped", preview=row[0][:20] if row[0] else "?")
    return trips


async def downgrade_stale_instants(max_age_hours: float) -> int:
    """
    Move instant-tier deals that were never sent (rate limit, route
    suppression, failed send) and are older than `max_age_hours` to the
    digest tier. Returns the number of deals moved.
    """
    path = await get_db_path()
    cutoff = (datetime.utcnow() - timedelta(hours=max_age_hours)).isoformat()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            UPDATE deals
               SET alert_tier = ?,
                   payload = json_set(payload, '$.alert_tier', ?)
             WHERE alert_tier = ? AND is_alerted = 0 AND created_at < ?
            """,
            (AlertTier.DIGEST.value, AlertTier.DIGEST.value, AlertTier.INSTANT.value, cutoff),
        )
        await db.commit()
        return cursor.rowcount or 0


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
            (AlertTier.INSTANT.value, AlertTier.DIGEST.value, cutoff, limit),
        )
        rows = await cursor.fetchall()
    trips = []
    for row in rows:
        try:
            trips.append(Trip.model_validate_json(row[0]))
        except Exception:
            log.warning("corrupted_trip_payload_skipped", preview=row[0][:20] if row[0] else "?")
    return trips


PriceRow = Tuple[str, float, str, str, str, str, str, str]
"""(route, price_eur, source, origin_city, dest_city, trip_type, nights_bucket, depart_month)"""


async def record_prices(rows: Sequence[PriceRow], cycle_id: str) -> None:
    """Append one cycle's observed prices in a single transaction."""
    if not rows:
        return
    now = datetime.utcnow().isoformat()
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.executemany(
            """
            INSERT INTO price_history
                (route, price_eur, source, origin_city, dest_city, trip_type, nights_bucket,
                 depart_month, recorded_at, cycle_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*row, now, cycle_id) for row in rows],
        )
        await db.commit()


async def load_price_history(since: datetime, exclude_cycle: str) -> List[Tuple[str, str, str, str, str, float, str]]:
    """
    Keyed observations recorded since `since`, excluding one cycle:
    (origin_city, dest_city, trip_type, nights_bucket, depart_month, price_eur, cycle_id).
    Rows from before the keyed schema (NULL keys) are ignored.
    """
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT origin_city, dest_city, trip_type, nights_bucket, depart_month, price_eur, cycle_id
            FROM price_history
            WHERE recorded_at >= ? AND cycle_id IS NOT NULL AND cycle_id != ?
              AND origin_city IS NOT NULL AND depart_month IS NOT NULL
            """,
            (since.isoformat(), exclude_cycle),
        )
        return list(await cursor.fetchall())


async def purge_old_rows(
    history_days: int = 120, expired_grace_days: int = 7, alerts_days: int = 30,
) -> Dict[str, int]:
    """Retention: drop old price history, deals long past expiry and old alert records."""
    now = datetime.utcnow()
    history_cutoff = (now - timedelta(days=history_days)).isoformat()
    expired_cutoff = (now - timedelta(days=expired_grace_days)).isoformat()
    alerts_cutoff = (now - timedelta(days=alerts_days)).isoformat()
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        prices = await db.execute("DELETE FROM price_history WHERE recorded_at < ?", (history_cutoff,))
        deals = await db.execute("DELETE FROM deals WHERE expires_at < ?", (expired_cutoff,))
        alerts = await db.execute("DELETE FROM alerts_sent WHERE sent_at < ?", (alerts_cutoff,))
        await db.commit()
    return {"price_history": prices.rowcount, "deals": deals.rowcount, "alerts_sent": alerts.rowcount}


async def get_recent_alert_timestamps(tier: AlertTier, within_hours: float) -> list:
    """Return UTC datetimes of alerts sent for this tier within the given window."""
    path = await get_db_path()
    cutoff = (datetime.utcnow() - timedelta(hours=within_hours)).isoformat()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT sent_at FROM alerts_sent WHERE alert_tier = ? AND sent_at >= ?",
            (AlertTier(tier).value, cutoff),
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


async def get_search_cursor(cursor_key: str) -> int:
    """Rotation position of the search planner for one source/universe."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute("SELECT position FROM search_cursor WHERE cursor_key = ?", (cursor_key,))
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def set_search_cursor(cursor_key: str, position: int) -> None:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            INSERT INTO search_cursor (cursor_key, position, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(cursor_key) DO UPDATE SET position = excluded.position, updated_at = excluded.updated_at
            """,
            (cursor_key, position, datetime.utcnow().isoformat()),
        )
        await db.commit()
