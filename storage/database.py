from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

import aiosqlite

from config import get_settings
from storage.models import AlertTier, DealType, Trip
from utils.logging_config import get_logger
from utils.timeutil import utcnow

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
    cycle_id       TEXT,
    depart_date    TEXT,
    return_date    TEXT
)
"""

# Columns added after the first release: added to existing databases by init_db
_PRICE_HISTORY_KEY_COLUMNS = (
    "origin_city", "dest_city", "trip_type", "nights_bucket", "depart_month", "cycle_id",
    "depart_date", "return_date",
)
# Similarity key for fuzzy dedup (filters/deduplication.py) and the departure date
_DEALS_DEDUP_COLUMNS = ("dedup_origin", "dedup_dest", "dedup_kind", "dedup_nights", "depart_date")

DedupKey = Tuple[str, str, str, str]
"""(origin city, destination city, kind, length profile or '') — see filters.deduplication.fingerprint"""

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

_CREATE_FEED_EXTRACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS feed_extractions (
    url         TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    status      TEXT NOT NULL,
    payload     TEXT,
    created_at  TEXT NOT NULL
)
"""

# Small key/value tables: engine state (poller offset, last cycle, pause) and
# preference overrides set from Telegram commands (values are JSON)
# One row per search cycle (B37): Activity's search history and "searches today"
_CREATE_CYCLES_TABLE = """
CREATE TABLE IF NOT EXISTS cycles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    duration_s   REAL,
    trigger      TEXT,
    fares        INTEGER,
    trips        INTEGER,
    new_instant  INTEGER,
    new_digest   INTEGER,
    sent         INTEGER,
    status       TEXT NOT NULL,
    error        TEXT
)
"""

_CREATE_ENGINE_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS engine_state (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL
)
"""

_CREATE_PREF_OVERRIDES_TABLE = """
CREATE TABLE IF NOT EXISTS pref_overrides (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL
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
    "CREATE INDEX IF NOT EXISTS idx_deals_dedup ON deals(dedup_origin, dedup_dest, dedup_kind, created_at)",
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
    """Add columns missing from an older database; each is "name" (TEXT) or "name TYPE"."""
    cursor = await db.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in await cursor.fetchall()}
    for column in columns:
        name, _, sql_type = column.partition(" ")
        if name not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type or 'TEXT'}")


async def init_db() -> None:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.execute(_CREATE_DEALS_TABLE)
        await _add_missing_columns(db, "deals", (*_DEALS_DEDUP_COLUMNS, "hidden"))
        await db.execute(_CREATE_ALERTS_TABLE)
        await _add_missing_columns(db, "alerts_sent", ("channel",))
        await db.execute(_CREATE_CYCLES_TABLE)
        await _add_missing_columns(db, "cycles", ("with_usual_price INTEGER",))
        await db.execute(_CREATE_PRICES_TABLE)
        await _add_missing_columns(db, "price_history", _PRICE_HISTORY_KEY_COLUMNS)
        await db.execute(_CREATE_SCRAPER_HEALTH_TABLE)
        await db.execute(_CREATE_SEARCH_CURSOR_TABLE)
        await db.execute(_CREATE_FEED_EXTRACTIONS_TABLE)
        await db.execute(_CREATE_ENGINE_STATE_TABLE)
        await db.execute(_CREATE_PREF_OVERRIDES_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await db.execute(idx_sql)
        await db.commit()


async def save_deal(trip: Trip, dedup_key: Optional[DedupKey] = None) -> bool:
    """Insert a deal; returns True if new, False if duplicate hash."""
    path = await get_db_path()
    expires = utcnow() + timedelta(days=7)
    key = dedup_key or (None, None, None, None)
    async with aiosqlite.connect(path) as db:
        try:
            await db.execute(
                """
                INSERT INTO deals
                    (hash, trip_id, route, deal_type, total_cost, discount_pct,
                     confidence, alert_tier, is_alerted, created_at, expires_at, payload,
                     dedup_origin, dedup_dest, dedup_kind, dedup_nights, depart_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    *key,
                    trip.departure_date.isoformat() if trip.departure_date else "",
                ),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


class SimilarDeal(NamedTuple):
    hash: str
    total_cost: float
    depart_date: Optional[date]
    alert_tier: str
    is_alerted: bool


async def find_similar_deals(key: DedupKey, since: datetime) -> List[SimilarDeal]:
    """Deals saved since `since` with the same dedup key."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT hash, total_cost, depart_date, alert_tier, is_alerted FROM deals
            WHERE dedup_origin = ? AND dedup_dest = ? AND dedup_kind = ? AND dedup_nights = ?
              AND created_at >= ?
            """,
            (*key, since.isoformat()),
        )
        rows = await cursor.fetchall()
    return [
        SimilarDeal(h, cost, date.fromisoformat(dep) if dep else None, tier, bool(alerted))
        for h, cost, dep, tier, alerted in rows
    ]


async def promote_deal(trip: Trip) -> bool:
    """
    Re-offer a saved, never-sent deal as instant (same hash: same price and
    dates) — e.g. a fare saved to the digest on a cold start that now stands
    out against the route's history. Resets created_at so the stale-instant
    sweep treats it as fresh, and refreshes the discount and confidence the
    instant queue sorts on (a cold-start deal was saved without a discount).
    """
    now = utcnow()
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            UPDATE deals SET alert_tier = ?, created_at = ?, expires_at = ?, payload = ?,
                             discount_pct = ?, confidence = ?
             WHERE hash = ? AND is_alerted = 0
            """,
            (AlertTier(trip.alert_tier).value, now.isoformat(), (now + timedelta(days=7)).isoformat(),
             trip.model_dump_json(), trip.discount_pct, trip.data_confidence_score, trip.hash),
        )
        await db.commit()
    return bool(cursor.rowcount)


async def archive_unsent(hashes: Sequence[str]) -> int:
    """Retire unsent deals superseded by a cheaper copy, so only the better price can alert."""
    if not hashes:
        return 0
    path = await get_db_path()
    marks = ",".join("?" * len(hashes))
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            f"UPDATE deals SET alert_tier = ? WHERE is_alerted = 0 AND hash IN ({marks})",
            (AlertTier.ARCHIVE.value, *hashes),
        )
        await db.commit()
    return cursor.rowcount


async def is_duplicate(trip_hash: str) -> bool:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM deals WHERE hash = ? LIMIT 1", (trip_hash,)
        )
        row = await cursor.fetchone()
        return row is not None


async def mark_alerted(trip_hash: str, tier: AlertTier, channel: str = "telegram") -> None:
    """Record a sent alert; `channel` names where it went ("telegram", "desktop" or "desktop,telegram")."""
    path = await get_db_path()
    now = utcnow().isoformat()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE deals SET is_alerted = 1 WHERE hash = ?", (trip_hash,)
        )
        await db.execute(
            "INSERT INTO alerts_sent (hash, alert_tier, sent_at, channel) VALUES (?, ?, ?, ?)",
            (trip_hash, AlertTier(tier).value, now, channel),
        )
        await db.commit()


async def hide_deal(trip_hash: str) -> bool:
    """Hide a deal from the digest and the instant queue (the app's "Hide this deal")."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute("UPDATE deals SET hidden = '1' WHERE hash = ?", (trip_hash,))
        await db.commit()
    return bool(cursor.rowcount)


async def count_alerts_sent_last_hour(tier: AlertTier) -> int:
    path = await get_db_path()
    cutoff = (utcnow() - timedelta(hours=1)).isoformat()
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
            WHERE alert_tier = ? AND is_alerted = 0 AND hidden IS NULL
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
    cutoff = (utcnow() - timedelta(hours=max_age_hours)).isoformat()
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
    cutoff = (utcnow() - timedelta(hours=24)).isoformat()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT payload FROM deals
            WHERE alert_tier IN (?, ?)
              AND created_at >= ? AND hidden IS NULL
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


# route, price, source, the four PriceKey fields, departure month, departure date,
# return date ("" for one-way)
PriceRow = Tuple[str, float, str, str, str, str, str, str, str, str]
"""(route, price_eur, source, origin_city, dest_city, trip_type, nights_bucket, depart_month)"""


async def record_prices(rows: Sequence[PriceRow], cycle_id: str) -> None:
    """Append one cycle's observed prices in a single transaction."""
    if not rows:
        return
    now = utcnow().isoformat()
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.executemany(
            """
            INSERT INTO price_history
                (route, price_eur, source, origin_city, dest_city, trip_type, nights_bucket,
                 depart_month, depart_date, return_date, recorded_at, cycle_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*row, now, cycle_id) for row in rows],
        )
        await db.commit()


async def load_price_history(
    since: datetime, exclude_cycle: str,
) -> List[Tuple[str, str, str, str, str, float, str, str, str]]:
    """
    Keyed observations recorded since `since`, excluding one cycle, oldest first:
    (origin_city, dest_city, trip_type, nights_bucket, itinerary, price_eur, cycle_id, day, depart_date).
    The itinerary ("MXP-KRK 2026-11-13 2026-11-16") identifies one fare however often it is
    seen; day is the YYYY-MM-DD it was recorded on. Rows from before fares were keyed or
    dated are ignored.
    """
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT origin_city, dest_city, trip_type, nights_bucket,
                   route || ' ' || depart_date || ' ' || COALESCE(return_date, ''),
                   price_eur, cycle_id, substr(recorded_at, 1, 10), depart_date
            FROM price_history
            WHERE recorded_at >= ? AND cycle_id IS NOT NULL AND cycle_id != ?
              AND origin_city IS NOT NULL AND depart_date IS NOT NULL
            ORDER BY recorded_at, id
            """,
            (since.isoformat(), exclude_cycle),
        )
        return list(await cursor.fetchall())


async def purge_old_rows(
    history_days: int = 120, expired_grace_days: int = 7, alerts_days: int = 30,
) -> Dict[str, int]:
    """Retention: drop old price history, deals long past expiry and old alert records."""
    now = utcnow()
    history_cutoff = (now - timedelta(days=history_days)).isoformat()
    expired_cutoff = (now - timedelta(days=expired_grace_days)).isoformat()
    alerts_cutoff = (now - timedelta(days=alerts_days)).isoformat()
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        prices = await db.execute("DELETE FROM price_history WHERE recorded_at < ?", (history_cutoff,))
        deals = await db.execute("DELETE FROM deals WHERE expires_at < ?", (expired_cutoff,))
        alerts = await db.execute("DELETE FROM alerts_sent WHERE sent_at < ?", (alerts_cutoff,))
        await db.execute("DELETE FROM feed_extractions WHERE created_at < ?", (alerts_cutoff,))
        await db.execute("DELETE FROM cycles WHERE started_at < ?", (alerts_cutoff,))
        await db.commit()
    return {"price_history": prices.rowcount, "deals": deals.rowcount, "alerts_sent": alerts.rowcount}


async def get_recent_alert_timestamps(tier: AlertTier, within_hours: float) -> list:
    """Return UTC datetimes of alerts sent for this tier within the given window."""
    path = await get_db_path()
    cutoff = (utcnow() - timedelta(hours=within_hours)).isoformat()
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
    cutoff = (utcnow() - timedelta(hours=within_hours)).isoformat()
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


async def get_feed_extractions(urls: Sequence[str]) -> Dict[str, Tuple[str, Optional[str]]]:
    """Cached AI extractions of deal-site posts: url → (status, payload JSON)."""
    if not urls:
        return {}
    path = await get_db_path()
    marks = ",".join("?" * len(urls))
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            f"SELECT url, status, payload FROM feed_extractions WHERE url IN ({marks})", tuple(urls)
        )
        return {url: (status, payload) for url, status, payload in await cursor.fetchall()}


async def save_feed_extraction(url: str, source: str, status: str, payload: Optional[str] = None) -> None:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            INSERT INTO feed_extractions (url, source, status, payload, created_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET status = excluded.status, payload = excluded.payload,
                                           created_at = excluded.created_at
            """,
            (url, source, status, payload, utcnow().isoformat()),
        )
        await db.commit()


async def _get_kv(table: str, key: str) -> Optional[str]:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(f"SELECT value FROM {table} WHERE key = ?", (key,))
        row = await cursor.fetchone()
    return row[0] if row else None


async def _set_kv(table: str, key: str, value: Optional[str]) -> None:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        if value is None:
            await db.execute(f"DELETE FROM {table} WHERE key = ?", (key,))
        else:
            await db.execute(
                f"""
                INSERT INTO {table} (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, utcnow().isoformat()),
            )
        await db.commit()


async def get_state(key: str) -> Optional[str]:
    return await _get_kv("engine_state", key)


async def set_state(key: str, value: Optional[str]) -> None:
    """Store (or with None, clear) an engine state value."""
    await _set_kv("engine_state", key, value)


async def get_pref_overrides() -> Dict[str, object]:
    """Preference overrides set from Telegram: key → decoded JSON value."""
    import json

    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        rows = await (await db.execute("SELECT key, value FROM pref_overrides")).fetchall()
    return {key: json.loads(value) for key, value in rows if value is not None}


async def set_pref_override(key: str, value: object) -> None:
    """Store an override as JSON; None removes it (back to the YAML value)."""
    import json

    await _set_kv("pref_overrides", key, None if value is None else json.dumps(value))


async def count_deals_since(since: datetime) -> int:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        row = await (await db.execute("SELECT COUNT(*) FROM deals WHERE created_at >= ?", (since.isoformat(),))).fetchone()
    return row[0]


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
            (cursor_key, position, utcnow().isoformat()),
        )
        await db.commit()


# ── Search cycles (B37) ───────────────────────────────────────────────────────

async def start_cycle_record(trigger: str) -> int:
    """Record a cycle that is starting; returns its id."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "INSERT INTO cycles (started_at, trigger, status) VALUES (?, ?, 'running')",
            (utcnow().isoformat(), trigger),
        )
        await db.commit()
    return cursor.lastrowid


async def finish_cycle_record(cycle_id: int, stats: Dict[str, object]) -> None:
    """Store a finished cycle: status ("ok", "failed", "stopped"), error, duration_s and its counts."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            UPDATE cycles SET finished_at = ?, duration_s = ?, fares = ?, trips = ?, new_instant = ?,
                              new_digest = ?, sent = ?, with_usual_price = ?, status = ?, error = ?
             WHERE id = ?
            """,
            (utcnow().isoformat(), stats.get("duration_s"), stats.get("fares"), stats.get("trips"),
             stats.get("new_instant"), stats.get("new_digest"), stats.get("sent"), stats.get("with_usual_price"),
             stats.get("status") or ("failed" if stats.get("error") else "ok"), stats.get("error"), cycle_id),
        )
        await db.commit()


async def close_interrupted_cycles() -> int:
    """Mark searches left 'running' by a crash or power cut as stopped (called when the engine starts)."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "UPDATE cycles SET status = 'stopped', error = 'interrupted' WHERE status = 'running'"
        )
        await db.commit()
    return cursor.rowcount


async def recent_cycles(limit: int = 20) -> List[Dict[str, object]]:
    """The latest cycles, newest first, as dicts of the cycles table's columns."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM cycles ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in await cursor.fetchall()]


async def count_cycles_since(since: datetime) -> int:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM cycles WHERE started_at >= ? AND status != 'running'", (since.isoformat(),)
        )
        return (await cursor.fetchone())[0]


# ── Queries for the app's screens (B38) ───────────────────────────────────────

class DealRow(NamedTuple):
    trip: Trip
    alert_tier: str
    is_alerted: bool
    created_at: str


def _deal_rows(rows) -> List[DealRow]:
    deals = []
    for payload, tier, alerted, created in rows:
        try:
            deals.append(DealRow(Trip.model_validate_json(payload), tier, bool(alerted), created))
        except Exception:
            log.warning("corrupted_trip_payload_skipped", preview=payload[:20] if payload else "?")
    return deals


async def get_recent_deals(hours: float = 24) -> List[DealRow]:
    """Instant and digest deals saved in the last `hours`, not hidden, newest first."""
    cutoff = (utcnow() - timedelta(hours=hours)).isoformat()
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT payload, alert_tier, is_alerted, created_at FROM deals
             WHERE alert_tier IN (?, ?) AND created_at >= ? AND hidden IS NULL
             ORDER BY created_at DESC
            """,
            (AlertTier.INSTANT.value, AlertTier.DIGEST.value, cutoff),
        )
        return _deal_rows(await cursor.fetchall())


async def get_deal(trip_hash: str) -> Optional[DealRow]:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT payload, alert_tier, is_alerted, created_at FROM deals WHERE hash = ?", (trip_hash,)
        )
        rows = _deal_rows(await cursor.fetchall())
    return rows[0] if rows else None


async def latest_alerts_for(hashes: Sequence[str]) -> Dict[str, Tuple[str, str]]:
    """hash -> (sent_at, channel) of the latest alert sent for each deal."""
    if not hashes:
        return {}
    marks = ",".join("?" * len(hashes))
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            f"SELECT hash, sent_at, COALESCE(channel, 'telegram') FROM alerts_sent WHERE hash IN ({marks}) "
            "ORDER BY id",
            tuple(hashes),
        )
        return {h: (at, channel) for h, at, channel in await cursor.fetchall()}


async def alert_log(limit: int = 50) -> List[Dict[str, object]]:
    """Sent alerts, newest first, with the deal's route and price when it is still saved."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT a.sent_at, a.alert_tier, COALESCE(a.channel, 'telegram') AS channel, a.hash,
                   d.route, d.total_cost
              FROM alerts_sent a LEFT JOIN deals d ON d.hash = a.hash
             ORDER BY a.id DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def count_pending_instants() -> int:
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM deals WHERE alert_tier = ? AND is_alerted = 0 AND hidden IS NULL",
            (AlertTier.INSTANT.value,),
        )
        return (await cursor.fetchone())[0]


async def fares_for_key(
    origin_city: str, dest_city: str, trip_type: str, nights_bucket: str, since: datetime,
) -> List[Tuple[str, str, str, float, str]]:
    """(route, depart_date, return_date, price, recorded_at) for one history key, oldest first."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT route, depart_date, COALESCE(return_date, ''), price_eur, recorded_at FROM price_history
             WHERE origin_city = ? AND dest_city = ? AND trip_type = ? AND nights_bucket = ?
               AND recorded_at >= ? AND depart_date IS NOT NULL
             ORDER BY recorded_at, id
            """,
            (origin_city, dest_city, trip_type, nights_bucket, since.isoformat()),
        )
        return list(await cursor.fetchall())


async def seen_fares(since: datetime) -> List[Tuple[str, str, str, float, str]]:
    """(route, depart_date, return_date, price, recorded_at) of every dated fare since `since`, oldest first."""
    path = await get_db_path()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute(
            """
            SELECT route, depart_date, COALESCE(return_date, ''), price_eur, recorded_at FROM price_history
             WHERE recorded_at >= ? AND depart_date IS NOT NULL
             ORDER BY recorded_at, id
            """,
            (since.isoformat(),),
        )
        return list(await cursor.fetchall())
