"""Ryanair fare-finder source: parsing (live-recorded fixture), requests, failures, pipeline."""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from scrapers import ryanair
from scrapers.base import ScrapeStatus
from scrapers.ryanair import RyanairScraper
from storage.models import SearchTask
from utils import airports

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ryanair_round_trip_bgy.json").read_text())
API = "https://www.ryanair.com/api/farfnd/v4/roundTripFares"


@pytest.fixture(autouse=True)
def no_spacing(monkeypatch):
    async def _no_pause(_):
        return None
    monkeypatch.setattr(ryanair, "_pause", _no_pause)


def _task(origin="BGY", dest=None) -> SearchTask:
    return SearchTask(origin=origin, destination=dest, depart_from=date(2026, 10, 9),
                      depart_to=date(2026, 10, 22), nights_min=2, nights_max=4)


def test_parse_live_fixture():
    results = RyanairScraper().parse_fares(FIXTURE)
    assert [(r.destination, r.price) for r in results] == [("KRK", 43.98), ("AHO", 29.98), ("BCN", 29.98)]
    krk = results[0]
    assert krk.origin == "BGY" and krk.currency == "EUR" and krk.airline == "Ryanair"
    assert krk.departure_date == date(2026, 10, 13) and krk.return_date == date(2026, 10, 17)
    assert krk.is_round_trip and krk.stops == 0 and krk.flight_number
    q = parse_qs(urlparse(krk.booking_url).query)
    assert q["originIata"] == ["BGY"] and q["destinationIata"] == ["KRK"]
    assert q["dateOut"] == ["2026-10-13"] and q["dateIn"] == ["2026-10-17"]
    assert krk.booking_url.startswith("https://www.ryanair.com/it/it/trip/flights/select?")


def test_unknown_airport_is_learned_from_the_response():
    data = copy.deepcopy(FIXTURE)
    arrival = data["fares"][0]["outbound"]["arrivalAirport"]
    arrival.update({"iataCode": "QZQ", "name": "Testisland", "city": {"name": "Testisland", "countryCode": "gr"}})
    RyanairScraper().parse_fares(data)
    assert airports.is_known("QZQ")
    assert airports.city_of("QZQ") == "Testisland"
    assert airports.is_europe("QZQ")


def test_request_params_for_anywhere_and_route_tasks():
    scraper = RyanairScraper()
    anywhere = scraper._request_params(_task())
    assert "arrivalAirportIataCode" not in anywhere
    assert anywhere["inboundDepartureDateFrom"] == "2026-10-11"   # depart_from + nights_min
    assert anywhere["inboundDepartureDateTo"] == "2026-10-26"     # depart_to + nights_max
    assert (anywhere["durationFrom"], anywhere["durationTo"]) == (2, 4)
    assert scraper._request_params(_task(dest="KRK"))["arrivalAirportIataCode"] == "KRK"


async def test_rate_limit_stops_the_cycle():
    with respx.mock() as router:
        route = router.get(API).mock(return_value=httpx.Response(429))
        outcome = await RyanairScraper().safe_scrape([_task("BGY"), _task("MXP")])
    assert outcome.status == ScrapeStatus.RATE_LIMITED
    assert route.call_count == 1


async def test_server_errors_are_reported():
    with respx.mock() as router:
        router.get(API).mock(return_value=httpx.Response(503))
        outcome = await RyanairScraper().safe_scrape([_task()])
    assert outcome.status == ScrapeStatus.ERROR
    assert "HTTP 503" in outcome.error


async def test_unserved_airport_is_empty_not_an_error():
    with respx.mock() as router:
        router.get(API).mock(return_value=httpx.Response(200, json={"fares": [], "size": 0}))
        outcome = await RyanairScraper().safe_scrape([_task("LIN")])
    assert outcome.status == ScrapeStatus.EMPTY


async def test_pipeline_alerts_real_ryanair_fares(engine):
    # Priority destination: without price history ordinary fares only reach the digest
    engine.set_prefs(priority_destinations=["KRK"])
    engine.router.get(API).mock(return_value=httpx.Response(200, json=FIXTURE))
    await engine.run_cycle(extra_flight_scrapers=[RyanairScraper()])

    alerts = [t for t in engine.telegram.texts if "FLIGHT DEAL" in t]
    assert alerts, engine.telegram.texts
    assert any("Ryanair" in t and "ryanair.com" in t for t in alerts)
    assert any("Milan → Krakow" in t and "€44" in t for t in alerts)
