"""Travelpayouts source (fixture built from the documented response schema)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from config import get_settings
from scrapers.base import ScrapeStatus
from scrapers.travelpayouts import TravelpayoutsScraper
from storage.models import SearchTask

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "travelpayouts_prices_for_dates_mil.json").read_text())
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "tp-test-token")
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "12345")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _task(origin="MXP", dest=None, start=date(2026, 10, 12), end=date(2026, 10, 25), nights=(2, 7)) -> SearchTask:
    return SearchTask(origin=origin, destination=dest, depart_from=start, depart_to=end,
                      nights_min=nights[0], nights_max=nights[1])


def test_disabled_without_token(monkeypatch):
    monkeypatch.delenv("TRAVELPAYOUTS_TOKEN", raising=False)
    get_settings.cache_clear()
    assert TravelpayoutsScraper().enabled is False
    get_settings.cache_clear()


def test_query_uses_the_city_code_and_month(token):
    assert TravelpayoutsScraper.query_for(_task("BGY")) == ("MIL", None, "2026-10")
    assert TravelpayoutsScraper.query_for(_task("KRK", "PRG")) == ("KRK", "PRG", "2026-10")


def test_parse_rows(token):
    results = TravelpayoutsScraper().parse_rows(FIXTURE["data"], _task())
    by_dest = {r.destination: r for r in results}
    assert set(by_dest) == {"KRK", "BCN", "JFK"}
    krk = by_dest["KRK"]
    assert krk.origin == "BGY"  # the actual airport, not the city code
    assert (krk.departure_date, krk.return_date) == (date(2026, 10, 15), date(2026, 10, 18))
    assert krk.airline == "Ryanair" and krk.flight_number == "FR3408" and krk.stops == 0
    assert krk.price_is_cached and krk.is_round_trip
    assert krk.booking_url.startswith("https://www.aviasales.com/search/MIL1510KRK18101?")
    assert krk.booking_url.endswith("&marker=12345")
    assert by_dest["JFK"].stops == 1


def test_rows_outside_the_task_are_dropped(token):
    weekend = TravelpayoutsScraper().parse_rows(FIXTURE["data"], _task(nights=(2, 4)))
    assert {r.destination for r in weekend} == {"KRK"}  # BCN is 6 nights, JFK 7
    early = TravelpayoutsScraper().parse_rows(FIXTURE["data"], _task(end=date(2026, 10, 15)))
    assert {r.destination for r in early} == {"KRK"}


async def test_tasks_sharing_a_query_make_one_request(token):
    with respx.mock() as router:
        route = router.get(API).mock(return_value=httpx.Response(200, json=FIXTURE))
        outcome = await TravelpayoutsScraper().safe_scrape([_task("MXP"), _task("LIN"), _task("BGY")])
    assert route.call_count == 1
    sent = route.calls[0].request
    assert sent.headers["X-Access-Token"] == "tp-test-token"
    assert dict(sent.url.params)["origin"] == "MIL"
    assert outcome.status == ScrapeStatus.OK and outcome.count == 3


async def test_invalid_token_is_reported(token):
    with respx.mock() as router:
        router.get(API).mock(return_value=httpx.Response(401, json={"message": "Unauthorized"}))
        outcome = await TravelpayoutsScraper().safe_scrape([_task()])
    assert outcome.status == ScrapeStatus.ERROR
    assert "invalid Travelpayouts token" in outcome.error


async def test_rate_limit_is_reported(token):
    with respx.mock() as router:
        router.get(API).mock(return_value=httpx.Response(429))
        outcome = await TravelpayoutsScraper().safe_scrape([_task()])
    assert outcome.status == ScrapeStatus.RATE_LIMITED


async def test_pipeline_alert_says_the_price_is_cached(engine, token):
    engine.router.get(API).mock(return_value=httpx.Response(200, json=FIXTURE))
    await engine.run_cycle(extra_flight_scrapers=[TravelpayoutsScraper()])

    krk = [t for t in engine.telegram.texts if "Milan → Krakow" in t and "FLIGHT DEAL" in t]
    assert krk, engine.telegram.texts
    assert "Cached price" in krk[0]
    assert "aviasales.com" in krk[0]
