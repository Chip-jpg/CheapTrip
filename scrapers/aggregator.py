from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple

from scrapers.base import BaseFlightScraper, BaseHotelScraper
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

    async def collect_flights(
        self, params: ScraperParams
    ) -> Tuple[List[RawFlightResult], dict]:
        """Returns (results, stats) where stats maps source → count."""
        expanded = _expand_params(params)
        tasks = [s.safe_scrape(expanded) for s in self._flight_scrapers]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results: List[RawFlightResult] = []
        stats: dict = {}
        for scraper, outcome in zip(self._flight_scrapers, gathered):
            if isinstance(outcome, list):
                count = len(outcome)
                stats[scraper.source_id] = count
                results.extend(outcome)
                await self._monitor.record_result(scraper.source_id, count, success=True)
            else:
                stats[scraper.source_id] = 0
                await self._monitor.record_result(scraper.source_id, 0, success=False)
                log.error(
                    "aggregator_flight_error",
                    source=scraper.source_id,
                    error=str(outcome),
                )

        log.info("flights_collected", total=len(results), sources=stats)
        return results, stats

    async def collect_hotels(
        self, params: ScraperParams
    ) -> Tuple[List[RawHotelResult], dict]:
        expanded = _expand_params(params)
        tasks = [s.safe_scrape(expanded) for s in self._hotel_scrapers]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results: List[RawHotelResult] = []
        stats: dict = {}
        for scraper, outcome in zip(self._hotel_scrapers, gathered):
            if isinstance(outcome, list):
                count = len(outcome)
                stats[scraper.source_id] = count
                results.extend(outcome)
                await self._monitor.record_result(scraper.source_id, count, success=True)
            else:
                stats[scraper.source_id] = 0
                await self._monitor.record_result(scraper.source_id, 0, success=False)
                log.error(
                    "aggregator_hotel_error",
                    source=scraper.source_id,
                    error=str(outcome),
                )

        log.info("hotels_collected", total=len(results), sources=stats)
        return results, stats
