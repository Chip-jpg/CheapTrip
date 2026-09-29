"""Google Flights: tfs encoding, label parsing (live-recorded fixtures), blocks and rate limits."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from scrapers import google_flights
from scrapers.base import ScrapeStatus
from scrapers.google_flights import (
    COOLDOWN_STATE_KEY,
    GoogleFlightsScraper,
    encode_search,
    parse_label,
    search_url,
)
from storage.database import get_state, init_db, set_state
from storage.models import SearchTask
from tests.harness import reset_caches
from utils import protobuf as pb

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_spacing(monkeypatch):
    async def _no_pause(_):
        return None
    monkeypatch.setattr(google_flights, "_pause", _no_pause)


@pytest.fixture(autouse=True)
def own_db(tmp_path, monkeypatch):
    """Each test gets its own database, where the cooldown after a block is stored."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'gf.db'}")
    reset_caches()
    asyncio.run(init_db())
    yield
    monkeypatch.undo()
    reset_caches()


def _task(dest="KRK", dep=date(2026, 11, 6), nights=2) -> SearchTask:
    return SearchTask(origin="MXP", destination=dest, depart_from=dep, depart_to=dep, nights_min=nights, nights_max=nights)


def test_search_encoding_round_trips():
    fields = pb.decode(pb.from_url_param(encode_search("MXP", "KRK", date(2026, 11, 6), date(2026, 11, 8))))
    legs = [pb.decode(leg) for leg in fields[3]]
    assert [leg[2][0] for leg in legs] == [b"2026-11-06", b"2026-11-08"]
    assert pb.decode(legs[0][13][0])[2] == [b"MXP"] and pb.decode(legs[0][14][0])[2] == [b"KRK"]
    assert pb.decode(legs[1][13][0])[2] == [b"KRK"]
    assert fields[19] == [1]            # round trip
    assert fields[8] == [1] and fields[9] == [1]


def test_one_way_encoding():
    fields = pb.decode(pb.from_url_param(encode_search("MXP", "KRK", date(2026, 11, 6), None)))
    assert len(fields[3]) == 1 and fields[19] == [2]


def test_search_url():
    q = parse_qs(urlparse(search_url("MXP", "KRK", date(2026, 11, 6), date(2026, 11, 8))).query)
    assert q["hl"] == ["en"] and q["curr"] == ["EUR"] and q["tfs"]


def test_parse_label_with_narrow_no_break_spaces():
    label = (
        "From 1,080 euros round trip total. 2 stops flight with Lufthansa and ANA. "
        "Leaves Milano Malpensa Airport at 7:05 AM on Friday, December 31 and arrives at "
        "Haneda Airport at 9:40 AM on Saturday, January 1. Total duration 20 hr 35 min."
    )
    it = parse_label(label, date(2026, 12, 31))
    assert it.price == 1080 and it.stops == 2 and it.airline == "Lufthansa and ANA"
    assert it.departure_time == datetime(2026, 12, 31, 7, 5)
    assert it.arrival_time == datetime(2027, 1, 1, 9, 40)  # rolls into next year
    assert it.duration_minutes == 20 * 60 + 35


def test_non_result_labels_are_ignored():
    assert parse_label("Price, Not selected", date(2026, 11, 6)) is None
    assert parse_label("Under €50, Price, Not selected", date(2026, 11, 6)) is None


def test_parse_live_fixture_europe():
    html = (FIXTURES / "google_flights_mxp_krk_rt.html").read_text()
    results = GoogleFlightsScraper().parse_results(html, _task())
    assert [(r.price, r.airline, r.stops) for r in results] == [
        (80.0, "Ryanair", 0), (113.0, "Wizz Air", 0), (173.0, "Air Dolomiti and Lufthansa", 1),
    ]
    cheapest = results[0]
    assert cheapest.departure_time == datetime(2026, 11, 6, 21, 15)
    assert cheapest.duration_minutes == 115
    assert cheapest.is_round_trip and cheapest.return_date == date(2026, 11, 8)
    assert cheapest.booking_url.startswith("https://www.google.com/travel/flights?tfs=")


def test_parse_live_fixture_long_haul():
    html = (FIXTURES / "google_flights_mxp_jfk_rt.html").read_text()
    results = GoogleFlightsScraper().parse_results(html, _task("JFK", date(2026, 11, 13), 7))
    assert results[0].price == 463.0 and results[0].airline == "American"
    assert results[0].duration_minutes == 9 * 60 + 4


async def test_results_from_the_live_fixture_via_http():
    html = (FIXTURES / "google_flights_mxp_krk_rt.html").read_text()
    with respx.mock() as router:
        route = router.get(url__startswith="https://www.google.com/travel/flights").mock(
            return_value=httpx.Response(200, text=html))
        outcome = await GoogleFlightsScraper().safe_scrape([_task()])
    assert outcome.status == ScrapeStatus.OK and outcome.count == 3
    assert "SOCS=" in route.calls[0].request.headers.get("cookie", "")


async def test_rate_limit_stops_the_cycle():
    with respx.mock() as router:
        route = router.get(url__startswith="https://www.google.com/travel/flights").mock(
            return_value=httpx.Response(429))
        outcome = await GoogleFlightsScraper().safe_scrape([_task("KRK"), _task("PRG")])
    assert outcome.status == ScrapeStatus.RATE_LIMITED
    assert route.call_count == 1


async def test_consent_page_is_reported_as_blocked():
    with respx.mock() as router:
        router.get(url__startswith="https://www.google.com/travel/flights").mock(
            return_value=httpx.Response(200, text="<html><h1>Before you continue to Google</h1></html>"))
        outcome = await GoogleFlightsScraper().safe_scrape([_task()])
    assert outcome.status == ScrapeStatus.BLOCKED


@contextmanager
def _google(response: httpx.Response) -> Iterator[respx.Route]:
    with respx.mock(assert_all_called=False) as router:
        yield router.get(url__startswith="https://www.google.com/travel/flights").mock(return_value=response)


async def test_block_starts_a_cooldown_then_the_source_comes_back():
    scraper = GoogleFlightsScraper()
    with _google(httpx.Response(429)) as route:
        await scraper.safe_scrape([_task()])
        await scraper.begin_cycle()
        cooling = await scraper.safe_scrape([_task()])
    assert route.call_count == 1, "no requests while cooling down"
    assert cooling.status == ScrapeStatus.DISABLED and "cooling down until" in cooling.error

    scraper._cooldown_until = datetime.utcnow() - timedelta(minutes=1)
    await set_state(COOLDOWN_STATE_KEY, scraper._cooldown_until.isoformat())
    await scraper.begin_cycle()
    html = (FIXTURES / "google_flights_mxp_krk_rt.html").read_text()
    with _google(httpx.Response(200, text=html)):
        back = await scraper.safe_scrape([_task()])
    assert back.status == ScrapeStatus.OK
    assert await get_state(COOLDOWN_STATE_KEY) is None


@pytest.mark.parametrize("block", [
    httpx.Response(429),
    httpx.Response(200, text="<html><h1>Before you continue to Google</h1></html>"),
])
async def test_cooldown_carries_over_to_the_next_process(block):
    # Round 3 dry run: separate `main.py cycle` runs forgot the cooldown
    with _google(block):
        await GoogleFlightsScraper().safe_scrape([_task()])
    assert await get_state(COOLDOWN_STATE_KEY)

    next_run = GoogleFlightsScraper()
    await next_run.begin_cycle()
    with _google(httpx.Response(200, text="<html></html>")) as route:
        outcome = await next_run.safe_scrape([_task()])

    assert route.call_count == 0
    assert outcome.status == ScrapeStatus.DISABLED and "cooling down until" in outcome.error


async def test_expired_stored_cooldown_lets_the_next_process_search():
    await set_state(COOLDOWN_STATE_KEY, (datetime.utcnow() - timedelta(minutes=1)).isoformat())

    scraper = GoogleFlightsScraper()
    await scraper.begin_cycle()

    assert scraper.enabled and scraper.disabled_reason is None
    assert await get_state(COOLDOWN_STATE_KEY) is None


async def test_unusable_database_keeps_the_cooldown_in_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'no_tables.db'}")
    reset_caches()
    scraper = GoogleFlightsScraper()
    with _google(httpx.Response(429)) as route:
        await scraper.safe_scrape([_task()])
        await scraper.begin_cycle()
        await scraper.safe_scrape([_task()])

    assert route.call_count == 1 and not scraper.enabled


async def test_health_shows_why_a_source_is_off(engine):
    from scrapers.base import ScrapeOutcome
    from scrapers.health_monitor import ScraperHealthMonitor
    from storage.database import init_db

    await init_db()
    monitor = ScraperHealthMonitor()
    await monitor.record(ScrapeOutcome("google_flights", ScrapeStatus.DISABLED, error="cooling down until 18:00 UTC"))

    assert "note: cooling down until 18:00 UTC" in await monitor.get_summary()
