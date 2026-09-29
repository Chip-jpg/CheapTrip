"""
End-to-end pipeline test harness.

Runs the real pipeline (normalize → build → filter → decay → dedup → notify)
against fake scrapers, a temporary SQLite database, a temporary preferences
file and a mocked Telegram Bot API that records every message the engine
would have sent. Use the `engine` fixture from conftest.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

import httpx
import yaml

from config import get_settings
from notifier.telegram import TelegramNotifier
from preferences import apply_overrides, get_preferences
from scrapers.aggregator import ScraperAggregator
from scrapers.base import BaseFeedScraper, BaseFlightScraper, BaseHotelScraper, SearchCapability
from storage.database import init_db
from storage.models import RawFlightResult, RawHotelResult, SearchTask

TEST_BOT_TOKEN = "123456:TEST-TOKEN"
TEST_CHAT_ID = "4242"
SEND_MESSAGE_URL = f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/sendMessage"
BOT_API = f"https://api.telegram.org/bot{TEST_BOT_TOKEN}"


def mock_doctor_telegram(router, chat: Optional[httpx.Response] = None, webhook: str = "") -> None:
    """The Telegram calls `main.py doctor` makes: getMe, getChat, getWebhookInfo."""
    router.get(f"{BOT_API}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "CheapTripBot"}}))
    router.get(f"{BOT_API}/getChat").mock(
        return_value=chat or httpx.Response(200, json={"ok": True, "result": {"id": 4242, "first_name": "Sam"}}))
    router.get(f"{BOT_API}/getWebhookInfo").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"url": webhook}}))

# Small search space so each cycle makes few scraper calls.
DEFAULT_PREFS: Dict[str, Any] = {
    "home_airports": ["MXP"],
    "preferred_trip_lengths": ["weekend"],
    "search_window_days": 20,
}


def reset_caches() -> None:
    """Drop cached Settings and UserPreferences so env/YAML changes take effect."""
    get_settings.cache_clear()
    apply_overrides({})  # also clears the preferences cache


def flight(
    origin: str = "MXP",
    destination: str = "KRK",
    price: float = 25.0,
    currency: str = "EUR",
    days_ahead: int = 14,
    nights: Optional[int] = 3,
    source: str = "skyscanner_api",
    airline: Optional[str] = "Ryanair",
    booking_url: Optional[str] = "https://example.com/book",
    **extra: Any,
) -> RawFlightResult:
    dep = date.today() + timedelta(days=days_ahead)
    return RawFlightResult(
        origin=origin,
        destination=destination,
        price=price,
        currency=currency,
        departure_date=dep,
        return_date=dep + timedelta(days=nights) if nights else None,
        airline=airline,
        booking_url=booking_url,
        source=source,
        **extra,
    )


def hotel(
    name: str = "Hotel Test",
    location: str = "Krakow",
    price_per_night: float = 30.0,
    nights: int = 3,
    rating: Optional[float] = 8.5,
    days_ahead: int = 14,
    source: str = "booking_com_api",
    booking_url: Optional[str] = "https://example.com/hotel",
    **extra: Any,
) -> RawHotelResult:
    check_in = date.today() + timedelta(days=days_ahead)
    return RawHotelResult(
        name=name,
        location=location,
        price_per_night=price_per_night,
        currency="EUR",
        nights=nights,
        rating=rating,
        booking_url=booking_url,
        source=source,
        check_in=check_in,
        check_out=check_in + timedelta(days=nights),
        **extra,
    )


ResultsSpec = Union[Sequence[Any], Callable[[List[SearchTask]], Sequence[Any]]]


def _materialize(spec: ResultsSpec, tasks: List[SearchTask]) -> List[Any]:
    results = spec(tasks) if callable(spec) else spec
    return [r.model_copy() for r in results]


class FakeFlightScraper(BaseFlightScraper):
    """
    Returns canned flight results once per cycle and records the planned
    tasks it received. Defaults to an 'anywhere' source with a large budget.
    """

    def __init__(
        self,
        results: ResultsSpec = (),
        source_id: str = "fake_flights",
        capability: SearchCapability = SearchCapability.ANYWHERE,
        budget: int = 1000,
        long_haul: bool = False,
    ) -> None:
        self.source_id = source_id
        self.enabled = True
        self.capability = capability
        self.long_haul = long_haul  # True: the planner also gives it searches from repositioning hubs
        self._budget = budget
        self._results = results
        self.calls: List[List[SearchTask]] = []

    def calls_per_cycle(self) -> int:
        return self._budget

    async def scrape(self, tasks: List[SearchTask]) -> List[RawFlightResult]:
        self.calls.append(tasks)
        return _materialize(self._results, tasks)


class FakeFeedScraper(BaseFeedScraper):
    """Deal-feed fake: returns canned (usually undated) deals and counts calls."""

    def __init__(self, results: Sequence[Any] = (), source_id: str = "fake_feed") -> None:
        self.source_id = source_id
        self.enabled = True
        self._results = results
        self.calls = 0

    async def scrape_feed(self) -> List[RawFlightResult]:
        self.calls += 1
        return [r.model_copy() for r in self._results]


class FakeHotelScraper(BaseHotelScraper):
    """Returns canned hotel results once per cycle and records the planned tasks."""

    def __init__(self, results: ResultsSpec = (), source_id: str = "fake_hotels", budget: int = 1000) -> None:
        self.source_id = source_id
        self.enabled = True
        self._budget = budget
        self._results = results
        self.calls: List[List[SearchTask]] = []

    def calls_per_cycle(self) -> int:
        return self._budget

    async def scrape(self, tasks: List[SearchTask]) -> List[RawHotelResult]:
        self.calls.append(tasks)
        return _materialize(self._results, tasks)


@dataclass
class TelegramCapture:
    """
    Records sendMessage calls made against the mocked Bot API.

    Replies 200 by default; queue_responses() scripts the next replies
    (an httpx.Response to return or an exception to raise, in order).
    """

    requests: List[httpx.Request] = field(default_factory=list)
    queued: List[Union[httpx.Response, Exception]] = field(default_factory=list)

    def queue_responses(self, *responses: Union[httpx.Response, Exception]) -> None:
        self.queued.extend(responses)

    def record(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.queued:
            nxt = self.queued.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.requests)}})

    @property
    def payloads(self) -> List[Dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]

    @property
    def texts(self) -> List[str]:
        return [p["text"] for p in self.payloads]


@dataclass
class Engine:
    """Handle returned by the `engine` fixture."""

    tmp_path: Path
    prefs_path: Path
    telegram: TelegramCapture
    router: Any = None  # the respx router: add routes to mock other HTTP APIs

    def set_prefs(self, **overrides: Any) -> None:
        data = dict(DEFAULT_PREFS)
        data.update(overrides)
        self.prefs_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        get_preferences.cache_clear()

    async def run_cycle(
        self,
        flights: ResultsSpec = (),
        hotels: ResultsSpec = (),
        feeds: Sequence[Any] = (),
        extra_flight_scrapers: Sequence[BaseFlightScraper] = (),
    ) -> ScraperAggregator:
        """
        Run one full pipeline cycle; returns the aggregator. Its scrapers are
        [FakeFlightScraper, FakeFeedScraper] and [FakeHotelScraper] — inspect their calls.
        """
        from scheduler.runner import run_pipeline_cycle

        await init_db()
        aggregator = ScraperAggregator(
            flight_scrapers=[FakeFlightScraper(flights), FakeFeedScraper(feeds), *extra_flight_scrapers],
            hotel_scrapers=[FakeHotelScraper(hotels)],
        )
        notifier = await TelegramNotifier.create()
        await run_pipeline_cycle(aggregator=aggregator, notifier=notifier)
        return aggregator

    async def run_digest(self) -> None:
        from scheduler.runner import run_daily_digest

        await init_db()
        notifier = await TelegramNotifier.create()
        await run_daily_digest(notifier=notifier)
