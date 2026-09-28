from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple

from scrapers.base import BaseFlightScraper, BaseHotelScraper, ScrapeOutcome, ScrapeStatus
from scrapers.booking_com import BookingComScraper
from scrapers.going import GoingScraper
from scrapers.google_flights import GoogleFlightsScraper
from scrapers.health_monitor import get_health_monitor
from scrapers.holiday_pirates import HolidayPiratesFlightScraper, HolidayPiratesHotelScraper
from scrapers.secret_flying import SecretFlyingScraper
from scrapers.skyscanner import SkyscannerScraper
from storage.models import RawFlightResult, RawHotelResult, ScraperParams
from utils.airport_clusters import expand_list_to_clusters
from utils.logging_config import get_logger

log = get_logger(__name__)


def build_flight_scrapers() -> List[BaseFlightScraper]:
    return [
        SkyscannerScraper(),
        GoogleFlightsScraper(),
        SecretFlyingScraper(),
        HolidayPiratesFlightScraper(),
        GoingScraper(),
    ]


def build_hotel_scrapers() -> List[BaseHotelScraper]:
    return [
        BookingComScraper(),
        HolidayPiratesHotelScraper(),
    ]


def _expand_params(params: ScraperParams) -> ScraperParams:
    """Return a copy of params with origins and destinations cluster-expanded."""
    expanded_origins = expand_list_to_clusters(params.origins)
    expanded_destinations = expand_list_to_clusters(params.destinations)
    return params.model_copy(update={
        "origins": expanded_origins,
        "destinations": expanded_destinations,
    })


class ScraperAggregator:
    """
    Runs all scrapers concurrently.
    Any scraper failure is isolated — pipeline continues with remaining results.
    Automatically expands airport clusters before dispatching.
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

    async def collect_flights(
        self, params: ScraperParams
    ) -> Tuple[List[RawFlightResult], dict]:
        """Search-based flight sources for one search window. Returns (results, stats by source)."""
        expanded = _expand_params(params)
        scrapers = [s for s in self._flight_scrapers if not s.is_feed]
        return await self._run_flight_scrapers(scrapers, expanded)

    async def collect_feed_flights(self) -> Tuple[List[RawFlightResult], dict]:
        """Deal-feed sources; called once per cycle. Returns (results, stats by source)."""
        scrapers = [s for s in self._flight_scrapers if s.is_feed]
        return await self._run_flight_scrapers(scrapers, None)

    async def _run_flight_scrapers(
        self, scrapers: List[BaseFlightScraper], params: Optional[ScraperParams]
    ) -> Tuple[List[RawFlightResult], dict]:
        results, stats = await self._run(scrapers, params)
        log.info("flights_collected", total=len(results), sources=stats)
        return results, stats

    async def collect_hotels(
        self, params: ScraperParams
    ) -> Tuple[List[RawHotelResult], dict]:
        results, stats = await self._run(self._hotel_scrapers, _expand_params(params))
        log.info("hotels_collected", total=len(results), sources=stats)
        return results, stats

    async def _run(self, scrapers: list, params: Optional[ScraperParams]) -> Tuple[list, dict]:
        """Run scrapers concurrently; record every outcome with the health monitor."""
        outcomes = await asyncio.gather(*(s.safe_scrape(params) for s in scrapers), return_exceptions=True)
        results: list = []
        stats: dict = {}
        for scraper, outcome in zip(scrapers, outcomes):
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
