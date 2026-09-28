from __future__ import annotations

import asyncio
import html
from datetime import datetime
from typing import Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ai_layer.formatter import format_best_finds_summary
from config import DECAY_INSTANT_CUTOFF, POPULAR_DESTINATIONS, get_settings
from filters.deduplication import deduplicate_trips
from filters.hard_filters import apply_hard_filters
from filters.time_decay import apply_time_decay
from normalizers.flight import normalize_flights
from normalizers.hotel import normalize_hotels
from notifier.telegram import TelegramNotifier
from preferences import get_preferences
from scrapers.aggregator import ScraperAggregator
from scrapers.health_monitor import get_health_monitor
from storage.database import (
    downgrade_stale_instants,
    get_digest_deals,
    get_pending_instant_alerts,
    init_db,
)
from storage.models import Trip
from trip_builder.builder import build_trips
from trip_builder.date_discovery import generate_search_windows
from utils.airport_clusters import expand_list_to_clusters
from utils.logging_config import get_logger

log = get_logger(__name__)

# The digest lists at most this many routes (best deal per route).
DIGEST_MAX_ROUTES = 15
# Unsent instant deals offered to the notifier per cycle (it stops at the hourly quota).
PENDING_INSTANT_LIMIT = 20

# Notifier is initialized async in run_forever() to restore rate-limiter state from DB.
# Fallback sync init is used for cycle/search commands that don't go through run_forever().
_notifier = TelegramNotifier()
# Built lazily so importing this module doesn't instantiate every scraper.
_aggregator: Optional[ScraperAggregator] = None


def _get_aggregator() -> ScraperAggregator:
    global _aggregator
    if _aggregator is None:
        _aggregator = ScraperAggregator()
    return _aggregator


async def init_notifier() -> None:
    """Re-initialize the notifier with DB-restored rate limiter state."""
    global _notifier
    _notifier = await TelegramNotifier.create()


async def run_pipeline_cycle(
    aggregator: Optional[ScraperAggregator] = None,
    notifier: Optional[TelegramNotifier] = None,
) -> None:
    """
    Full pipeline execution:
    1. Load user preferences + generate flexible date windows
    2. Scrape flights + hotels (cluster-expanded airports)
    3. Normalize to EUR + confidence scores
    4. Build trips (direct, complete, repositioned)
    5. Apply hard filters (quality gate + feasibility gate)
    6. Apply time decay
    7. Deduplicate
    8. Send instant alerts (up to quota)

    `aggregator` and `notifier` default to the module-level instances; tests inject fakes.
    """
    aggregator = aggregator or _get_aggregator()
    notifier = notifier or _notifier
    cycle_start = datetime.utcnow()
    log.info("pipeline_cycle_start", ts=cycle_start.isoformat())

    try:
        prefs = get_preferences()

        # Cluster-expand home airports from preferences
        origins = expand_list_to_clusters(prefs.home_airports)
        excluded = set(prefs.excluded_destinations)
        destinations = [d for d in POPULAR_DESTINATIONS if d not in excluded]

        # Generate flexible date windows based on preferred trip lengths
        param_batches = generate_search_windows(
            prefs=prefs,
            origins=origins,
            destinations=destinations,
            max_price_eur=prefs.max_trip_budget or 2000.0,
        )

        log.info(
            "search_windows_generated",
            batches=len(param_batches),
            origins=len(origins),
            destinations=len(destinations),
        )

        # Collect from all scrapers across all date windows
        all_raw_flights = []
        all_raw_hotels = []

        for params in param_batches:
            flight_result, hotel_result = await asyncio.gather(
                aggregator.collect_flights(params),
                aggregator.collect_hotels(params),
                return_exceptions=True,
            )
            if isinstance(flight_result, Exception):
                log.warning("collect_flights_failed", error=str(flight_result))
            else:
                all_raw_flights.extend(flight_result[0])
            if isinstance(hotel_result, Exception):
                log.warning("collect_hotels_failed", error=str(hotel_result))
            else:
                all_raw_hotels.extend(hotel_result[0])

        log.info(
            "raw_collected",
            flights=len(all_raw_flights),
            hotels=len(all_raw_hotels),
        )

        # ── Normalize ─────────────────────────────────────────────────────────
        normalize_result = await asyncio.gather(
            normalize_flights(all_raw_flights),
            normalize_hotels(all_raw_hotels),
            return_exceptions=True,
        )
        flight_legs = normalize_result[0] if not isinstance(normalize_result[0], Exception) else []
        hotel_deals = normalize_result[1] if not isinstance(normalize_result[1], Exception) else []
        if isinstance(normalize_result[0], Exception):
            log.warning("normalize_flights_failed", error=str(normalize_result[0]))
        if isinstance(normalize_result[1], Exception):
            log.warning("normalize_hotels_failed", error=str(normalize_result[1]))

        # ── Build trips ───────────────────────────────────────────────────────
        trips = await build_trips(flight_legs, hotel_deals)
        new_instant: List[Trip] = []
        new_digest: List[Trip] = []
        if trips:
            # ── Hard filters ──────────────────────────────────────────────────
            instant_candidates, digest_candidates = apply_hard_filters(trips)

            # ── Time decay ────────────────────────────────────────────────────
            instant_fresh, digest_fresh, _ = apply_time_decay(instant_candidates)
            all_digest = digest_fresh + digest_candidates

            # ── Deduplication (new deals are saved unsent) ────────────────────
            new_instant, _ = await deduplicate_trips(instant_fresh)
            new_digest, _ = await deduplicate_trips(all_digest)

            log.info(
                "pipeline_filtered",
                new_instant=len(new_instant),
                new_digest=len(new_digest),
            )
        else:
            log.info("no_trips_built_this_cycle")

        # ── Send instant alerts ───────────────────────────────────────────────
        # New instant deals were just saved unsent; the pending queue also
        # re-offers earlier ones held back by the rate limit or a failed send.
        stale = await downgrade_stale_instants(DECAY_INSTANT_CUTOFF)
        if stale:
            log.info("stale_instants_moved_to_digest", count=stale)
        pending = await get_pending_instant_alerts(limit=PENDING_INSTANT_LIMIT)
        if pending:
            sent = await notifier.process_instant_queue(pending)
            log.info("instant_alerts_sent", count=sent, queued=len(pending))

        # ── Send cycle summary of best cheap finds ───────────────────────────
        all_found = new_instant + new_digest
        if all_found and not new_instant:
            await _send_best_finds_summary(all_found, notifier)

        await _log_health_summary()

        duration = (datetime.utcnow() - cycle_start).total_seconds()
        log.info("pipeline_cycle_complete", duration_s=round(duration, 1))

    except Exception as exc:
        log.error("pipeline_cycle_failed", error=str(exc), exc_info=True)
        # Do NOT crash the scheduler — just log


async def _send_best_finds_summary(trips: list, notifier: TelegramNotifier) -> None:
    """Send a Telegram summary of the cheapest deals found this cycle."""
    try:
        await notifier.send_system_message(format_best_finds_summary(trips, total_found=len(trips)))
    except Exception as exc:
        log.warning("best_finds_summary_failed", error=str(exc))


async def _log_health_summary() -> None:
    """Log a brief scraper health summary after each cycle."""
    try:
        monitor = get_health_monitor()
        report = await monitor.get_health_report()
        failing = [h.source_id for h in report if h.status in ("FAILING", "STALE")]
        if failing:
            log.warning("scrapers_degraded", sources=failing)
        else:
            ok = sum(1 for h in report if h.status == "OK")
            log.info("scraper_health_ok", ok_count=ok, total=len(report))
    except Exception:
        pass


def select_digest_trips(trips: List[Trip], limit: int = DIGEST_MAX_ROUTES) -> List[Trip]:
    """
    Keep the cheapest deal per route, then order routes by data confidence
    (desc) and price (asc) so the digest isn't one route on many dates.
    """
    best: Dict[str, Trip] = {}
    for trip in trips:
        current = best.get(trip.route)
        if current is None or trip.total_cost_eur < current.total_cost_eur:
            best[trip.route] = trip
    ranked = sorted(best.values(), key=lambda t: (-t.data_confidence_score, t.total_cost_eur))
    return ranked[:limit]


async def run_daily_digest(notifier: Optional[TelegramNotifier] = None) -> None:
    """Send the daily digest of best deals."""
    notifier = notifier or _notifier
    log.info("digest_run_start")
    try:
        trips = select_digest_trips(await get_digest_deals(limit=100))
        if trips:
            await notifier.send_digest(trips)
        else:
            log.info("digest_no_deals_to_send")
    except Exception as exc:
        log.error("digest_failed", error=str(exc), exc_info=True)


def create_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    # Main scraping cycle
    scheduler.add_job(
        run_pipeline_cycle,
        trigger=IntervalTrigger(minutes=settings.scrape_interval_minutes),
        id="pipeline_cycle",
        name="Main scraping + alert pipeline",
        replace_existing=True,
        max_instances=1,  # Prevent overlapping runs
        misfire_grace_time=300,
    )

    # Daily digest
    scheduler.add_job(
        run_daily_digest,
        trigger=CronTrigger(
            hour=settings.digest_hour,
            minute=settings.digest_minute,
            timezone=settings.timezone,
        ),
        id="daily_digest",
        name="Daily deal digest",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler


async def run_forever() -> None:
    """
    Main entry point for continuous operation.
    Initializes DB, starts scheduler, runs pipeline immediately on boot.
    """
    await init_db()
    log.info("database_initialized")

    # Re-initialize notifier now that DB is ready — restores rate-limiter state
    global _notifier
    _notifier = await TelegramNotifier.create()

    prefs = get_preferences()
    scheduler = create_scheduler()
    scheduler.start()
    log.info(
        "scheduler_started",
        interval_min=get_settings().scrape_interval_minutes,
        home_airports=prefs.home_airports,
        trip_lengths=prefs.preferred_trip_lengths,
        search_window_days=prefs.search_window_days,
    )

    await _notifier.send_system_message(
        "🚀 <b>Travel Deal Intelligence Engine started</b>\n"
        f"Origins: {html.escape(', '.join(prefs.home_airports))}\n"
        f"Profiles: {html.escape(', '.join(prefs.preferred_trip_lengths))}\n"
        f"Monitoring {len(POPULAR_DESTINATIONS)} destinations"
    )

    # Run immediately on startup
    await run_pipeline_cycle()

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler_stopping")
        scheduler.shutdown(wait=False)
