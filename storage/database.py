from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import AsyncIterator, Dict, List, NamedTuple, Optional, Sequence, Tuple

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
    "CREATE INDEX IF NOT EXISTS idx_prices_recorded ON price_history(recorded_at)",
    # fares_for_key: the "similar fares" behind each deal's usual price and charts
    "CREATE INDEX IF NOT EXISTS idx_prices_key"
    " ON price_history(origin_city, dest_city, trip_type, nights_bucket, recorded_at)",
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


# ── Connections ───────────────────────────────────────────────────────────────
# Opening a connection costs ~2 ms (a thread and the file), a query on an open one ~0.2 ms, and a
# search makes ~1,900 of them: every function here shares one connection instead (B46). A lock
# gives each `async with connection()` block the connection to itself, and what a block leaves
# uncommitted is rolled back, as closing its own connection did. Long reads (the price history
# summary, the Destinations figures) take a short-lived connection of their own so the small
# queries don't wait; in WAL mode they read while a search writes.

class _Shared:
    def __init__(self, path: str, loop: asyncio.AbstractEventLoop) -> None:
        self.db: Optional[aiosqlite.Connection] = None  # set once opened, under the lock
        self.path, self.loop = path, loop
        self.lock = asyncio.Lock()
        self.holder: Optional[asyncio.Task] = None


_shared: Optional[_Shared] = None


async def _open(path: str) -> aiosqlite.Connection:
    db = aiosqlite.connect(path)
    db._thread.daemon = True  # an idle connection never keeps the app from exiting (aiosqlite 0.22)
    await db
    await db.execute("PRAGMA journal_mode=WAL")  # readers and the writer don't wait for each other
    await db.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; commits don't wait for the disk
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA journal_size_limit=16777216")  # the WAL file shrinks back to 16 MB after a big write
    return db


async def _current() -> _Shared:
    """The shared connection, opened (again) for this event loop and database file."""
    global _shared
    path, loop = await get_db_path(), asyncio.get_running_loop()
    if _shared is not None and (_shared.path != path or _shared.loop is not loop):
        await close_connections()  # another database (tests) or event loop (one per CLI command)
    if _shared is None:
        shared = _shared = _Shared(path, loop)
        async with shared.lock:  # whoever else asks meanwhile waits for it to open
            try:
                shared.db = await _open(path)
            except BaseException:
                _shared = None
                raise
    return _shared


@asynccontextmanager
async def connection(own: bool = False) -> AsyncIterator[aiosqlite.Connection]:
    """
    The database connection for one unit of work. `own=True` opens a short-lived
    connection of its own, for a long read that shouldn't hold up everything else.
    """
    if own:
        db = await _open(await get_db_path())
        try:
            yield db
        finally:
            await db.close()
        return
    shared = await _current()
    task = asyncio.current_task()
    if shared.holder is task:
        raise RuntimeError("connection() used inside another connection() block: pass the connection along")
    async with shared.lock:
        if shared.db is None:
            raise RuntimeError("the database connection failed to open")
        shared.holder = task
        db = shared.db
        try:
            yield db
        finally:
            shared.holder = None
            try:
                if db.in_transaction:
                    await db.rollback()  # as closing its own connection discarded it
            finally:
                db.row_factory = None


async def close_connections() -> None:
    """Close the shared connection (the engine stopping); the next use opens it again."""
    global _shared
    shared, _shared = _shared, None
    if shared is None or shared.db is None:
        return
    try:
        if shared.loop is asyncio.get_running_loop():
            async with shared.lock:  # a query in progress finishes first
                await shared.db.close()
        else:  # from an event loop that's gone (a CLI command, a test): nothing can be using it
            await shared.db.close()
    except Exception as exc:  # its thread is a daemon either way
        log.debug("connection_close_failed", error=str(exc))


async def _add_missing_columns(db: aiosqlite.Connection, table: str, columns: Sequence[str]) -> None:
    """Add columns missing from an older database; each is "name" (TEXT) or "name TYPE"."""
    cursor = await db.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in await cursor.fetchall()}
    for column in columns:
        name, _, sql_type = column.partition(" ")
        if name not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type or 'TEXT'}")


async def init_db() -> None:
    async with connection() as db:
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
        await db.execute("DROP INDEX IF EXISTS idx_prices_route")  # no query used it (B46)
        await db.commit()


async def save_deal(trip: Trip, dedup_key: Optional[DedupKey] = None) -> bool:
    """Insert a deal; returns True if new, False if duplicate hash."""
    expires = utcnow() + timedelta(days=7)
    key = dedup_key or (None, None, None, None)
    async with connection() as db:
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
    async with connection() as db:
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
    async with connection() as db:
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
    marks = ",".join("?" * len(hashes))
    async with connection() as db:
        cursor = await db.execute(
            f"UPDATE deals SET alert_tier = ? WHERE is_alerted = 0 AND hash IN ({marks})",
            (AlertTier.ARCHIVE.value, *hashes),
        )
        await db.commit()
    return cursor.rowcount


async def is_duplicate(trip_hash: str) -> bool:
    async with connection() as db:
        cursor = await db.execute(
            "SELECT 1 FROM deals WHERE hash = ? LIMIT 1", (trip_hash,)
        )
        row = await cursor.fetchone()
        return row is not None


async def mark_alerted(trip_hash: str, tier: AlertTier, channel: str = "telegram") -> None:
    """Record a sent alert; `channel` names where it went ("telegram", "desktop" or "desktop,telegram")."""
    now = utcnow().isoformat()
    async with connection() as db:
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
    async with connection() as db:
        cursor = await db.execute("UPDATE deals SET hidden = '1' WHERE hash = ?", (trip_hash,))
        await db.commit()
    return bool(cursor.rowcount)


async def count_alerts_sent_last_hour(tier: AlertTier) -> int:
    cutoff = (utcnow() - timedelta(hours=1)).isoformat()
    async with connection() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM alerts_sent WHERE alert_tier = ? AND sent_at >= ?",
            (AlertTier(tier).value, cutoff),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_pending_instant_alerts(limit: int = 10) -> List[Trip]:
    async with connection() as db:
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
    cutoff = (utcnow() - timedelta(hours=max_age_hours)).isoformat()
    async with connection() as db:
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
    cutoff = (utcnow() - timedelta(hours=24)).isoformat()
    async with connection() as db:
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
    async with connection() as db:
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


async def load_fare_summaries(
    since: datetime, exclude_cycle: str,
) -> List[Tuple[str, str, str, str, str, str, float, str, str, int, str, str, int]]:
    """
    One row per fare recorded since `since`, excluding one cycle (B46: the database
    sums up the history instead of handing over every row):
    (origin_city, dest_city, trip_type, nights_bucket, itinerary, depart_date, latest price,
     first cycle, last cycle, cycles, first day, last day, days).
    The itinerary ("MXP-KRK 2026-11-13 2026-11-16") identifies one fare however often it
    is seen; its latest price is from the row last recorded (then the highest id); days
    are the YYYY-MM-DD it was recorded on. Rows from before fares were keyed or dated are
    ignored. A connection of its own: a month of history takes a second or two.
    """
    async with connection(own=True) as db:
        cursor = await db.execute(
            """
            WITH fares AS (
                SELECT origin_city, dest_city, trip_type, nights_bucket,
                       route || ' ' || depart_date || ' ' || COALESCE(return_date, '') AS itinerary,
                       depart_date,
                       MAX(recorded_at || char(1) || printf('%020d', id)) AS latest,
                       MIN(cycle_id) AS first_cycle, MAX(cycle_id) AS last_cycle,
                       COUNT(DISTINCT cycle_id) AS cycles,
                       MIN(substr(recorded_at, 1, 10)) AS first_day, MAX(substr(recorded_at, 1, 10)) AS last_day,
                       COUNT(DISTINCT substr(recorded_at, 1, 10)) AS days
                FROM price_history
                WHERE recorded_at >= ? AND cycle_id IS NOT NULL AND cycle_id != ?
                  AND origin_city IS NOT NULL AND depart_date IS NOT NULL
                GROUP BY origin_city, dest_city, trip_type, nights_bucket, itinerary
            )
            SELECT f.origin_city, f.dest_city, f.trip_type, f.nights_bucket, f.itinerary, f.depart_date,
                   p.price_eur, f.first_cycle, f.last_cycle, f.cycles, f.first_day, f.last_day, f.days
            FROM fares f
            JOIN price_history p ON p.id = CAST(substr(f.latest, instr(f.latest, char(1)) + 1) AS INTEGER)
            """,
            (since.isoformat(), exclude_cycle),
        )
        return list(await cursor.fetchall())


# Price history kept: baselines, charts and Destinations look 30 days back (B46: was 120)
HISTORY_DAYS = 45
# Give the space freed by the purge back to the disk (VACUUM) when at least this much of the
# file is free, at most once a day: the file otherwise never shrinks
VACUUM_MIN_FREE_SHARE = 0.25
VACUUM_MIN_FREE_MB = 20


async def purge_old_rows(
    history_days: int = HISTORY_DAYS, expired_grace_days: int = 7, alerts_days: int = 30,
    vacuum_min_free_mb: float = VACUUM_MIN_FREE_MB,
) -> Dict[str, int]:
    """Retention: drop old price history, deals long past expiry and old alert records."""
    now = utcnow()
    history_cutoff = (now - timedelta(days=history_days)).isoformat()
    expired_cutoff = (now - timedelta(days=expired_grace_days)).isoformat()
    alerts_cutoff = (now - timedelta(days=alerts_days)).isoformat()
    async with connection() as db:
        prices = await db.execute("DELETE FROM price_history WHERE recorded_at < ?", (history_cutoff,))
        deals = await db.execute("DELETE FROM deals WHERE expires_at < ?", (expired_cutoff,))
        alerts = await db.execute("DELETE FROM alerts_sent WHERE sent_at < ?", (alerts_cutoff,))
        await db.execute("DELETE FROM feed_extractions WHERE created_at < ?", (alerts_cutoff,))
        await db.execute("DELETE FROM cycles WHERE started_at < ?", (alerts_cutoff,))
        await db.commit()
    purged = {"price_history": prices.rowcount, "deals": deals.rowcount, "alerts_sent": alerts.rowcount}
    freed = await _vacuum_if_worth_it(vacuum_min_free_mb)
    if freed:
        purged["vacuumed_mb"] = freed
    return purged


async def _vacuum_if_worth_it(min_free_mb: float) -> int:
    """VACUUM when enough of the file is free space, at most once a day; returns the MB given back."""
    today = utcnow().date().isoformat()
    if await get_state("last_vacuum") == today:
        return 0
    async with connection() as db:
        page_size, pages, free = [
            (await (await db.execute(f"PRAGMA {name}")).fetchone())[0]
            for name in ("page_size", "page_count", "freelist_count")
        ]
        if not pages or free / pages < VACUUM_MIN_FREE_SHARE or free * page_size < min_free_mb * 2**20:
            return 0
        try:
            await db.execute("VACUUM")
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as exc:  # busy or out of disk: the purge still counts, and it's tried again tomorrow
            log.warning("vacuum_failed", error=str(exc))
            return 0
    await set_state("last_vacuum", today)
    freed = round(free * page_size / 2**20)
    log.info("database_vacuumed", freed_mb=freed)
    return freed


async def clear_price_history() -> int:
    """Forget every price seen (Settings → danger zone): usual prices are learned again from scratch."""
    async with connection() as db:
        deleted = await db.execute("DELETE FROM price_history")
        await db.commit()
    return deleted.rowcount


async def get_recent_alert_timestamps(tier: AlertTier, within_hours: float) -> list:
    """Return UTC datetimes of alerts sent for this tier within the given window."""
    cutoff = (utcnow() - timedelta(hours=within_hours)).isoformat()
    async with connection() as db:
        cursor = await db.execute(
            "SELECT sent_at FROM alerts_sent WHERE alert_tier = ? AND sent_at >= ?",
            (AlertTier(tier).value, cutoff),
        )
        rows = await cursor.fetchall()
    return [datetime.fromisoformat(r[0]) for r in rows]


async def was_route_alerted_recently(route: str, within_hours: float = 6.0) -> bool:
    """True if any deal on this exact route was alerted within the window."""
    cutoff = (utcnow() - timedelta(hours=within_hours)).isoformat()
    async with connection() as db:
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
    marks = ",".join("?" * len(urls))
    async with connection() as db:
        cursor = await db.execute(
            f"SELECT url, status, payload FROM feed_extractions WHERE url IN ({marks})", tuple(urls)
        )
        return {url: (status, payload) for url, status, payload in await cursor.fetchall()}


async def save_feed_extraction(url: str, source: str, status: str, payload: Optional[str] = None) -> None:
    async with connection() as db:
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
    async with connection() as db:
        cursor = await db.execute(f"SELECT value FROM {table} WHERE key = ?", (key,))
        row = await cursor.fetchone()
    return row[0] if row else None


async def _set_kv(table: str, key: str, value: Optional[str]) -> None:
    async with connection() as db:
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

    async with connection() as db:
        rows = await (await db.execute("SELECT key, value FROM pref_overrides")).fetchall()
    return {key: json.loads(value) for key, value in rows if value is not None}


async def set_pref_override(key: str, value: object) -> None:
    """Store an override as JSON; None removes it (back to the YAML value)."""
    import json

    await _set_kv("pref_overrides", key, None if value is None else json.dumps(value))


async def count_deals_since(since: datetime) -> int:
    async with connection() as db:
        row = await (await db.execute("SELECT COUNT(*) FROM deals WHERE created_at >= ?", (since.isoformat(),))).fetchone()
    return row[0]


async def get_search_cursor(cursor_key: str) -> int:
    """Rotation position of the search planner for one source/universe."""
    async with connection() as db:
        cursor = await db.execute("SELECT position FROM search_cursor WHERE cursor_key = ?", (cursor_key,))
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def set_search_cursor(cursor_key: str, position: int) -> None:
    async with connection() as db:
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
    async with connection() as db:
        cursor = await db.execute(
            "INSERT INTO cycles (started_at, trigger, status) VALUES (?, ?, 'running')",
            (utcnow().isoformat(), trigger),
        )
        await db.commit()
    return cursor.lastrowid


async def finish_cycle_record(cycle_id: int, stats: Dict[str, object]) -> None:
    """Store a finished cycle: status ("ok", "failed", "stopped"), error, duration_s and its counts."""
    async with connection() as db:
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
    async with connection() as db:
        cursor = await db.execute(
            "UPDATE cycles SET status = 'stopped', error = 'interrupted' WHERE status = 'running'"
        )
        await db.commit()
    return cursor.rowcount


async def recent_cycles(limit: int = 20) -> List[Dict[str, object]]:
    """The latest cycles, newest first, as dicts of the cycles table's columns."""
    async with connection() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM cycles ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in await cursor.fetchall()]


async def count_cycles_since(since: datetime) -> int:
    async with connection() as db:
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
    async with connection() as db:
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
    async with connection() as db:
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
    async with connection() as db:
        cursor = await db.execute(
            f"SELECT hash, sent_at, COALESCE(channel, 'telegram') FROM alerts_sent WHERE hash IN ({marks}) "
            "ORDER BY id",
            tuple(hashes),
        )
        return {h: (at, channel) for h, at, channel in await cursor.fetchall()}


async def alert_log(limit: int = 50) -> List[Dict[str, object]]:
    """Sent alerts, newest first, with the deal's route and price when it is still saved."""
    async with connection() as db:
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
    async with connection() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM deals WHERE alert_tier = ? AND is_alerted = 0 AND hidden IS NULL",
            (AlertTier.INSTANT.value,),
        )
        return (await cursor.fetchone())[0]


async def fares_for_key(
    origin_city: str, dest_city: str, trip_type: str, nights_bucket: str, since: datetime,
) -> List[Tuple[str, str, str, float, str]]:
    """(route, depart_date, return_date, price, recorded_at) for one history key, oldest first."""
    async with connection() as db:
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


async def destination_figures(
    since: datetime,
) -> Tuple[List[Tuple[str, str, str, float, str]], List[Tuple[str, str, float]]]:
    """
    The Destinations screen's figures, summed up by the database (B46): every fare
    seen since `since` at its latest (route, depart_date, return_date, price,
    recorded_at), and each destination's cheapest price per day (code, YYYY-MM-DD,
    price). A connection of its own: a month of history takes a second.
    """
    async with connection(own=True) as db:
        cursor = await db.execute(
            """
            WITH fares AS (
                SELECT MAX(recorded_at || char(1) || printf('%020d', id)) AS latest
                FROM price_history
                WHERE recorded_at >= ? AND depart_date IS NOT NULL
                GROUP BY route, depart_date, COALESCE(return_date, '')
            )
            SELECT p.route, p.depart_date, COALESCE(p.return_date, ''), p.price_eur, p.recorded_at
            FROM fares f
            JOIN price_history p ON p.id = CAST(substr(f.latest, instr(f.latest, char(1)) + 1) AS INTEGER)
            ORDER BY p.recorded_at, p.id
            """,
            (since.isoformat(),),
        )
        fares = list(await cursor.fetchall())
        cursor = await db.execute(
            """
            SELECT CASE WHEN instr(route, '-') > 0 THEN substr(route, instr(route, '-') + 1) ELSE route END,
                   substr(recorded_at, 1, 10), MIN(price_eur)
            FROM price_history
            WHERE recorded_at >= ? AND depart_date IS NOT NULL
            GROUP BY 1, 2
            """,
            (since.isoformat(),),
        )
        daily = list(await cursor.fetchall())
    return fares, daily


async def price_history_mark() -> Optional[int]:
    """The newest saved price's id: it changes whenever prices are added or cleared (ids aren't reused)."""
    async with connection() as db:
        cursor = await db.execute("SELECT MAX(id) FROM price_history")
        row = await cursor.fetchone()
    return row[0] if row else None
