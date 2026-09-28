from __future__ import annotations

import importlib.util
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

import httpx

from storage.models import RawFlightResult, RawHotelResult, SearchTask
from utils.logging_config import get_logger

log = get_logger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def _accept_encoding() -> str:
    """Only advertise encodings httpx can decode: br needs the brotli package."""
    if any(importlib.util.find_spec(m) for m in ("brotli", "brotlicffi")):
        return "gzip, deflate, br"
    return "gzip, deflate"


BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    "Accept-Encoding": _accept_encoding(),
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


class SearchCapability(str, Enum):
    """What one request to a source can answer; the planner builds tasks to match."""
    ROUTE_DATE = "route_date"    # one origin → one destination, exact dates
    ROUTE_MONTH = "route_month"  # one origin → one destination, a month of departures
    ANYWHERE = "anywhere"        # one origin → all destinations, a departure range
    CITY_DATES = "city_dates"    # hotels: one city, check-in date + nights
    FEED = "feed"                # deal feeds: no search parameters, once per cycle


class ScrapeStatus(str, Enum):
    OK = "ok"                      # returned results
    EMPTY = "empty"                # ran cleanly, nothing found
    PARTIAL = "partial"            # some requests failed, some results
    ERROR = "error"                # failed (exception, or every request failed)
    DISABLED = "disabled"          # turned off in settings / missing key
    RATE_LIMITED = "rate_limited"  # the source asked us to back off
    BLOCKED = "blocked"            # bot protection / consent wall


@dataclass
class ScrapeOutcome:
    source_id: str
    status: ScrapeStatus
    results: List[Any] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: int = 0

    @property
    def count(self) -> int:
        return len(self.results)


class _OutcomeReporting:
    """
    Shared safe_scrape(): never raises, and turns what happened into a
    ScrapeOutcome. Scrapers that catch their own per-request errors call
    _record_error() so those failures still show up in health reports, and
    _report_status() for rate limits and blocks.
    """

    source_id: str = "unknown"
    enabled: bool = True
    capability: SearchCapability = SearchCapability.ROUTE_DATE

    def begin_cycle(self) -> None:
        """Called once at the start of each pipeline cycle (reset per-cycle budgets)."""

    def calls_per_cycle(self) -> int:
        """How many search tasks the planner may give this source per cycle."""
        return 10

    def _record_error(self, message: str) -> None:
        self.__dict__.setdefault("_errors", []).append(message)

    def _report_status(self, status: ScrapeStatus, message: Optional[str] = None) -> None:
        self.__dict__["_status_override"] = status
        if message:
            self._record_error(message)

    async def scrape(self, tasks: List[SearchTask]) -> List[Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    async def safe_scrape(self, tasks: List[SearchTask]) -> ScrapeOutcome:
        if not self.enabled:
            return ScrapeOutcome(self.source_id, ScrapeStatus.DISABLED)

        self.__dict__["_errors"] = []
        self.__dict__["_status_override"] = None
        started = time.monotonic()
        try:
            results = await self.scrape(tasks)
        except Exception as exc:
            log.error("scraper_failed", source=self.source_id, error=str(exc))
            return ScrapeOutcome(
                self.source_id, ScrapeStatus.ERROR, error=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        errors: List[str] = self.__dict__.get("_errors") or []
        status = self.__dict__.get("_status_override")
        if status is None:
            if errors:
                status = ScrapeStatus.PARTIAL if results else ScrapeStatus.ERROR
            else:
                status = ScrapeStatus.OK if results else ScrapeStatus.EMPTY
        outcome = ScrapeOutcome(
            self.source_id, status, list(results),
            error="; ".join(errors[:3]) or None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        log.info("scraper_done", source=self.source_id, status=status.value, count=outcome.count)
        return outcome


class BaseFlightScraper(_OutcomeReporting, ABC):
    @property
    def is_feed(self) -> bool:
        return self.capability == SearchCapability.FEED

    @abstractmethod
    async def scrape(self, tasks: List[SearchTask]) -> List[RawFlightResult]:
        """Search the given tasks; never raise — log, _record_error() and return what was found."""


class BaseFeedScraper(BaseFlightScraper):
    """
    Deal feeds publish posts, not search results: a route, a price and
    usually a travel window instead of a date. They are collected once per
    cycle (ScraperAggregator.collect_feed_flights) rather than per search
    window, and never invent a departure date.
    """

    capability = SearchCapability.FEED

    @abstractmethod
    async def scrape_feed(self) -> List[RawFlightResult]:
        """Return deals from the feed; never raise — log and return []."""

    async def scrape(self, tasks: Optional[List[SearchTask]] = None) -> List[RawFlightResult]:
        return await self.scrape_feed()


class BaseHotelScraper(_OutcomeReporting, ABC):
    capability: SearchCapability = SearchCapability.CITY_DATES

    @property
    def is_feed(self) -> bool:
        return self.capability == SearchCapability.FEED

    @abstractmethod
    async def scrape(self, tasks: List[SearchTask]) -> List[RawHotelResult]:
        """Search the given hotel tasks; never raise — log, _record_error() and return what was found."""
