from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import List, Optional

import httpx

from storage.models import RawFlightResult, RawHotelResult, ScraperParams
from utils.logging_config import get_logger

log = get_logger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:124.0) Gecko/20100101 Firefox/124.0",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
}


def random_headers(extra: dict | None = None) -> dict:
    h = dict(BASE_HEADERS)
    h["User-Agent"] = random.choice(_USER_AGENTS)
    if extra:
        h.update(extra)
    return h


def build_client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        http2=True,
        headers=random_headers(),
    )


class BaseFlightScraper(ABC):
    source_id: str = "unknown"
    enabled: bool = True
    # Feed scrapers (deal blogs/newsletters) ignore search params and run once per cycle.
    is_feed: bool = False

    def begin_cycle(self) -> None:
        """Called once at the start of each pipeline cycle (reset per-cycle budgets)."""

    @abstractmethod
    async def scrape(self, params: ScraperParams) -> List[RawFlightResult]:
        """Return a list of raw flight results; never raise — log and return []."""

    async def safe_scrape(self, params: ScraperParams) -> List[RawFlightResult]:
        if not self.enabled:
            return []
        try:
            results = await self.scrape(params)
            log.info("scraper_done", source=self.source_id, count=len(results))
            return results
        except Exception as exc:
            log.error("scraper_failed", source=self.source_id, error=str(exc))
            return []


class BaseFeedScraper(BaseFlightScraper):
    """
    Deal feeds publish posts, not search results: a route, a price and
    usually a travel window instead of a date. They are collected once per
    cycle (ScraperAggregator.collect_feed_flights) rather than per search
    window, and never invent a departure date.
    """

    is_feed = True

    @abstractmethod
    async def scrape_feed(self) -> List[RawFlightResult]:
        """Return deals from the feed; never raise — log and return []."""

    async def scrape(self, params: Optional[ScraperParams] = None) -> List[RawFlightResult]:
        return await self.scrape_feed()


class BaseHotelScraper(ABC):
    source_id: str = "unknown"
    enabled: bool = True

    def begin_cycle(self) -> None:
        """Called once at the start of each pipeline cycle (reset per-cycle budgets)."""

    @abstractmethod
    async def scrape(self, params: ScraperParams) -> List[RawHotelResult]:
        """Return a list of raw hotel results; never raise — log and return []."""

    async def safe_scrape(self, params: ScraperParams) -> List[RawHotelResult]:
        if not self.enabled:
            return []
        try:
            results = await self.scrape(params)
            log.info("hotel_scraper_done", source=self.source_id, count=len(results))
            return results
        except Exception as exc:
            log.error("hotel_scraper_failed", source=self.source_id, error=str(exc))
            return []
