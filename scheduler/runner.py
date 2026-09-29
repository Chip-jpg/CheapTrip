from __future__ import annotations

import asyncio
import html
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ai_layer.formatter import format_best_finds_summary
from config import DECAY_INSTANT_CUTOFF, get_settings
from control import LAST_CYCLE_KEY, load_overrides, paused_until
from filters.deduplication import collapse_similar, deduplicate_trips
from filters.hard_filters import apply_hard_filters
from filters.time_decay import apply_time_decay
from normalizers.flight import normalize_flights
from normalizers.hotel import normalize_hotels
from notifier.channels import ChannelNotifier
from notifier.telegram import TelegramNotifier
from preferences import get_preferences
from scheduler.planner import SearchPlanner
from scrapers.aggregator import ScraperAggregator
from scrapers.health_monitor import get_health_monitor
from storage.database import (
    downgrade_stale_instants,
    finish_cycle_record,
    get_digest_deals,
    get_pending_instant_alerts,
    purge_old_rows,
    set_state,
    start_cycle_record,
)
from storage.models import Trip
from trip_builder.builder import build_trips
from utils.events import get_event_bus
from utils.logging_config import get_logger
from utils.timeutil import utcnow

log = get_logger(__name__)

# The digest lists at most this many routes (best deal per route).
DIGEST_MAX_ROUTES = 15
# Unsent instant deals offered to the notifier per cycle (it stops at the hourly quota).
PENDING_INSTANT_LIMIT = 20

# Either sends alerts; ChannelNotifier (desktop + Telegram) is what the engine uses.
Notifier = Union[ChannelNotifier, TelegramNotifier]

# Notifier is initialized async (init_notifier, Engine.start) to restore rate-limiter state from DB.
# Fallback sync init is used for cycle/search commands that don't go through the engine.
_notifier: Notifier = ChannelNotifier(TelegramNotifier())
# Built lazily so importing this module doesn't instantiate every scraper.
_aggregator: Optional[ScraperAggregator] = None
_planner = SearchPlanner()


def get_aggregator() -> ScraperAggregator:
    """The scrapers the scheduled searches use, built on first use."""
    global _aggregator
    if _aggregator is None:
        _aggregator = ScraperAggregator()
    return _aggregator


_get_aggregator = get_aggregator


def reset_aggregator() -> None:
    """Rebuild the scrapers on the next search (their settings or keys changed)."""
    global _aggregator
    _aggregator = None


async def init_notifier() -> None:
    """Re-initialize the notifier with DB-restored rate limiter state."""
    global _notifier
    _notifier = await ChannelNotifier.create()


async def run_pipeline_cycle(
    aggregator: Optional[ScraperAggregator] = None,
    notifier: Optional[Notifier] = None,
    trigger: str = "scheduled",
) -> Dict[str, Any]:
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
    Every cycle is recorded in the cycles table and announced on the event bus
    (cycle_started, cycle_planned, source_done per source, cycle_finished).
    Returns the cycle's counts: fares, trips, new_instant, new_digest, sent,
    duration_s, status ("ok", "failed" or "stopped") and error.
    """
    aggregator = aggregator or _get_aggregator()
    notifier = notifier or _notifier
    cycle_start = utcnow()
    log.info("pipeline_cycle_start", ts=cycle_start.isoformat(), trigger=trigger)
    bus = get_event_bus()
    stats: Dict[str, Any] = {
        "trigger": trigger, "status": "ok", "error": None,
        "fares": 0, "trips": 0, "new_instant": 0, "new_digest": 0, "sent": 0,
    }
    cycle_id: Optional[int] = None
    try:
        cycle_id = await start_cycle_record(trigger)
    except Exception as exc:  # the history is nice to have; never block a cycle on it
        log.warning("cycle_record_failed", error=str(exc))
    bus.publish("cycle_started", trigger=trigger)

    try:
        await load_overrides()  # /mute, /priority, /budget from Telegram
        prefs = get_preferences()
        await aggregator.begin_cycle()
        try:
            purged = await purge_old_rows()
            if any(purged.values()):
                log.info("retention_purged", **purged)
        except Exception as exc:
            log.warning("retention_purge_failed", error=str(exc))

        # Plan this cycle's searches (budgets + rotation) for every source
        plan = await _planner.plan(prefs, aggregator.sources)
        bus.publish("cycle_planned", sources=[
            s.source_id for s in aggregator.sources if s.enabled and (s.is_feed or plan.for_source(s.source_id))
        ])

        # Deal feeds (no search params) run once; search sources run their planned tasks
        feed_result, flight_result, hotel_result = await asyncio.gather(
            aggregator.collect_feed_flights(),
            aggregator.collect_flights(plan),
            aggregator.collect_hotels(plan),
            return_exceptions=True,
        )
        all_raw_flights = []
        all_raw_hotels = []
        for name, result, bucket in (
            ("feed_flights", feed_result, all_raw_flights),
            ("flights", flight_result, all_raw_flights),
            ("hotels", hotel_result, all_raw_hotels),
        ):
            if isinstance(result, Exception):
                log.warning("collect_failed", kind=name, error=str(result))
            else:
                bucket.extend(result[0])

        log.info(
            "raw_collected",
            flights=len(all_raw_flights),
            hotels=len(all_raw_hotels),
        )
        stats["fares"] = len(all_raw_flights) + len(all_raw_hotels)

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
        trips = collapse_similar(await build_trips(flight_legs, hotel_deals))
        stats["trips"] = len(trips)
        stats["with_usual_price"] = sum(1 for t in trips if t.normal_price_eur is not None)
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
        stats["new_instant"], stats["new_digest"] = len(new_instant), len(new_digest)

        # ── Send instant alerts ───────────────────────────────────────────────
        # New instant deals were just saved unsent; the pending queue also
        # re-offers earlier ones held back by the rate limit or a failed send.
        stale = await downgrade_stale_instants(DECAY_INSTANT_CUTOFF)
        if stale:
            log.info("stale_instants_moved_to_digest", count=stale)
        paused = await paused_until()
        pending = await get_pending_instant_alerts(limit=PENDING_INSTANT_LIMIT)
        if paused:
            # /pause: deals are still found and saved; unsent instants reach the digest after the sweep
            log.info("alerts_paused", until=paused.isoformat(), pending=len(pending))
        elif pending:
            sent = await notifier.process_instant_queue(pending)
            stats["sent"] = sent
            log.info("instant_alerts_sent", count=sent, queued=len(pending))

        # ── Send cycle summary of best cheap finds ───────────────────────────
        all_found = new_instant + new_digest
        if all_found and not new_instant and not paused:
            await _send_best_finds_summary(all_found, notifier)

        await _report_health(notifier)
        await set_state(LAST_CYCLE_KEY, utcnow().isoformat())

        duration = (utcnow() - cycle_start).total_seconds()
        log.info("pipeline_cycle_complete", duration_s=round(duration, 1))

    except asyncio.CancelledError:  # the engine stopped (app quit, docker stop) during the search
        stats["status"] = "stopped"
        raise
    except Exception as exc:
        stats["status"], stats["error"] = "failed", str(exc) or type(exc).__name__
        log.error("pipeline_cycle_failed", error=str(exc), exc_info=True)
        # Do NOT crash the scheduler — just log
    finally:
        stats["duration_s"] = round((utcnow() - cycle_start).total_seconds(), 1)
        if cycle_id is not None:
            try:
                await finish_cycle_record(cycle_id, stats)
            except Exception as exc:
                log.warning("cycle_record_failed", error=str(exc))
        bus.publish("cycle_finished", **stats)
    return stats


async def _send_best_finds_summary(trips: list, notifier: Notifier) -> None:
    """Send a Telegram summary of the cheapest deals found this cycle."""
    try:
        await notifier.send_system_message(format_best_finds_summary(trips, total_found=len(trips)))
    except Exception as exc:
        log.warning("best_finds_summary_failed", error=str(exc))


async def _report_health(notifier: Notifier) -> None:
    """Log scraper health after each cycle; notify once when a source starts failing or recovers."""
    try:
        monitor = get_health_monitor()
        report = await monitor.get_health_report()
        unhealthy = [h.source_id for h in report if h.status in ("FAILING", "STALE", "DEGRADED")]
        if unhealthy:
            log.warning("scrapers_degraded", sources=unhealthy)
        else:
            ok = sum(1 for h in report if h.status == "OK")
            log.info("scraper_health_ok", ok_count=ok, total=len(report))

        for health, change in await monitor.pending_notifications():
            if not get_settings().notify_source_problems:  # switched off in Settings: nothing to send
                await monitor.mark_notified(health.source_id, health.status)
                continue
            if change == "failing":
                text = (
                    f"⚠️ <b>Source failing:</b> {html.escape(health.source_id)}\n"
                    f"{health.consecutive_failures} failed runs in a row"
                    f" (last: {html.escape(health.last_status or '?')})"
                )
                if health.last_error:
                    text += f"\n<i>{html.escape(health.last_error[:200])}</i>"
                notice = (f"{health.source_id} isn't working",
                          f"{health.consecutive_failures} searches failed in a row. Deals from it may be missing.")
            else:
                text = f"✅ <b>Source recovered:</b> {html.escape(health.source_id)}"
                notice = (f"{health.source_id} works again", "Its deals are back in your searches.")
            if await notifier.send_system_message(text, notice=notice):
                await monitor.mark_notified(health.source_id, health.status)
    except Exception as exc:
        log.warning("health_report_failed", error=str(exc))


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


async def run_daily_digest(notifier: Optional[Notifier] = None) -> None:
    """Send the daily digest of best deals."""
    notifier = notifier or _notifier
    log.info("digest_run_start")
    try:
        until = await paused_until()
        if until:
            log.info("digest_paused", until=until.isoformat())
            return
        trips = select_digest_trips(await get_digest_deals(limit=100))
        if trips:
            await notifier.send_digest(trips)
        else:
            log.info("digest_no_deals_to_send")
    except Exception as exc:
        log.error("digest_failed", error=str(exc), exc_info=True)


def create_scheduler(
    cycle_job: Callable[[], Awaitable[Any]] = run_pipeline_cycle,
    digest_job: Callable[[], Awaitable[Any]] = run_daily_digest,
) -> AsyncIOScheduler:
    """The search cycle every SCRAPE_INTERVAL_MINUTES and the daily digest (the Engine passes its own jobs)."""
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    # Main scraping cycle
    scheduler.add_job(
        cycle_job,
        trigger=IntervalTrigger(minutes=settings.scrape_interval_minutes),
        id="pipeline_cycle",
        name="Main scraping + alert pipeline",
        replace_existing=True,
        max_instances=1,  # Prevent overlapping runs
        misfire_grace_time=300,
    )

    # Daily digest
    scheduler.add_job(
        digest_job,
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
    """Headless 24/7 operation (`main.py run`, Docker): the Engine until SIGTERM or SIGINT."""
    from scheduler.engine import Engine

    await Engine().run()
