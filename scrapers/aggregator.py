from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple

from scheduler.planner import CyclePlan
from scrapers.base import BaseFlightScraper, BaseHotelScraper, ScrapeOutcome, ScrapeStatus
from scrapers.booking_com import BookingComScraper
from scrapers.going import GoingScraper
from scrapers.google_flights import GoogleFlightsScraper
from scrapers.google_hotels import GoogleHotelsScraper
from scrapers.health_monitor import get_health_monitor
from scrapers.holiday_pirates import HolidayPiratesFlightScraper, HolidayPiratesHotelScraper
from scrapers.ryanair import RyanairScraper
from scrapers.secret_flying import SecretFlyingScraper
from scrapers.skyscanner import SkyscannerScraper
from scrapers.travelpayouts import TravelpayoutsScraper
from storage.models import RawFlightResult, RawHotelResult
from utils.logging_config import get_logger

log = get_logger(__name__)


def build_flight_scrapers() -> List[BaseFlightScraper]:
    return [
        RyanairScraper(),
        TravelpayoutsScraper(),
        SkyscannerScraper(),
        GoogleFlightsScraper(),
        SecretFlyingScraper(),
        HolidayPiratesFlightScraper(),
        GoingScraper(),
    ]


def build_hotel_scrapers() -> List[BaseHotelScraper]:
    return [
        GoogleHotelsScraper(),
        BookingComScraper(),
        HolidayPiratesHotelScraper(),
    ]


class ScraperAggregator:
    """
    Runs scrapers concurrently with the tasks the search planner assigned them.
    Any scraper failure is isolated — the pipeline continues with the rest —
    and every outcome is recorded with the health monitor.
    """

    def __init__(
        self,
        flight_scrapers: Optional[List[BaseFlightScraper]] = None,
        hotel_scrapers: Optional[List[BaseHotelScraper]] = None,
    ) -> None:
        self._flight_scrapers = build_flight_scrapers() if flight_scrapers is None else flight_scrapers
        self._hotel_scrapers = build_hotel_scrapers() if hotel_scrapers is None else hotel_scrapers
        self._monitor = get_health_monitor()

    def begin_cycle(self) -> None:
        for scraper in [*self._flight_scrapers, *self._hotel_scrapers]:
            scraper.begin_cycle()

    @property
    def sources(self) -> list:
        return [*self._flight_scrapers, *self._hotel_scrapers]

    async def collect_flights(self, plan: CyclePlan) -> Tuple[List[RawFlightResult], dict]:
        """Search-based flight sources with this cycle's planned tasks. Returns (results, stats)."""
        results, stats = await self._run([s for s in self._flight_scrapers if not s.is_feed], plan)
        log.info("flights_collected", total=len(results), sources=stats)
        return results, stats

    async def collect_feed_flights(self) -> Tuple[List[RawFlightResult], dict]:
        """Deal-feed sources; called once per cycle. Returns (results, stats by source)."""
        results, stats = await self._run([s for s in self._flight_scrapers if s.is_feed], None)
        for result in results:
            result.is_feed_deal = True
        log.info("feed_flights_collected", total=len(results), sources=stats)
        return results, stats

    async def collect_hotels(self, plan: CyclePlan) -> Tuple[List[RawHotelResult], dict]:
        results, stats = await self._run(self._hotel_scrapers, plan)
        log.info("hotels_collected", total=len(results), sources=stats)
        return results, stats

    async def _run(self, scrapers: list, plan: Optional[CyclePlan]) -> Tuple[list, dict]:
        """
        Run scrapers concurrently with their planned tasks (feeds take none) and
        record every outcome with the health monitor. Enabled search sources
        with no tasks this cycle are skipped.
        """
        runnable = []
        for scraper in scrapers:
            tasks = [] if plan is None or scraper.is_feed else plan.for_source(scraper.source_id)
            if scraper.enabled and not scraper.is_feed and not tasks:
                continue
            runnable.append((scraper, tasks))

        outcomes = await asyncio.gather(
            *(s.safe_scrape(tasks) for s, tasks in runnable), return_exceptions=True
        )
        results: list = []
        stats: dict = {}
        for (scraper, _), outcome in zip(runnable, outcomes):
            if isinstance(outcome, BaseException):
                # safe_scrape never raises; this is a bug guard, not a normal path
                outcome = ScrapeOutcome(scraper.source_id, ScrapeStatus.ERROR, error=str(outcome))
            stats[scraper.source_id] = outcome.count
            results.extend(outcome.results)
            try:
                await self._monitor.record(outcome)
            except Exception as exc:
                log.warning("health_record_failed", source=scraper.source_id, error=str(exc))
        return results, stats
