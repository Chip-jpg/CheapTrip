"""Preferences beyond Milan (B22): market, adults, hubs, Italian-only sources."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from preferences import UserPreferences
from scrapers.google_flights import GoogleFlightsScraper
from scrapers.piratinviaggio import PiratinViaggioScraper
from scrapers.ryanair import RyanairScraper
from scrapers.skyscanner import _skyscanner_market
from storage.models import SearchTask
from tests.harness import flight
from utils.markets import first_home_airport, home_country, market_for

FIXTURES = Path(__file__).parent / "fixtures"


def _task(adults: int = 1) -> SearchTask:
    dep = date(2026, 11, 6)
    return SearchTask(origin="MXP", destination="KRK", depart_from=dep, depart_to=dep,
                      nights_min=2, nights_max=2, adults=adults)


@pytest.mark.parametrize("homes,market", [
    (["MXP", "BGY"], "it-it"),
    (["LGW"], "en-gb"),
    (["DUB"], "en-ie"),
    (["BCN"], "es-es"),
    (["JFK"], "en-gb"),  # no Ryanair site for the US: English (UK)
])
def test_market_follows_the_home_country(homes, market):
    assert market_for(UserPreferences(home_airports=homes)) == market


def test_market_preference_wins():
    prefs = UserPreferences(home_airports=["MXP"], market="EN-GB")
    assert market_for(prefs) == "en-gb"
    assert home_country(prefs) == "IT" and first_home_airport(prefs) == "MXP"


def test_london_user_gets_uk_markets_and_no_italian_feed(engine, monkeypatch):
    engine.set_prefs(home_airports=["LGW"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from config import get_settings
    get_settings.cache_clear()

    assert RyanairScraper()._request_params(_task())["market"] == "en-gb"
    assert _skyscanner_market() == "UK"
    reader = PiratinViaggioScraper()
    assert reader.enabled is False and reader.disabled_reason == "home airports are not in Italy"


def test_ryanair_market_setting_overrides_preferences(engine, monkeypatch):
    engine.set_prefs(home_airports=["LGW"])
    monkeypatch.setenv("RYANAIR_MARKET", "de-de")
    from config import get_settings
    get_settings.cache_clear()

    assert RyanairScraper()._request_params(_task())["market"] == "de-de"


async def test_searches_carry_the_number_of_adults(engine):
    engine.set_prefs(adults=2)

    aggregator = await engine.run_cycle(flights=[])

    tasks = aggregator._flight_scrapers[0].calls[0]
    assert tasks and all(t.adults == 2 for t in tasks)


def test_google_party_total_becomes_a_per_person_price():
    html = (FIXTURES / "google_flights_mxp_krk_rt.html").read_text()
    one = GoogleFlightsScraper().parse_results(html, _task(adults=1))
    two = GoogleFlightsScraper().parse_results(html, _task(adults=2))

    assert [r.price for r in two] == [round(r.price / 2, 2) for r in one]
    assert two[0].booking_url != one[0].booking_url  # the link searches for both travellers


async def test_alerts_say_per_person_and_hotel_links_carry_the_party(engine):
    engine.set_prefs(adults=2)

    await engine.run_cycle(flights=[flight(destination="KRK", price=20.0, nights=3)])

    [alert] = [t for t in engine.telegram.texts if "FLIGHT DEAL" in t]
    assert "€20</b> per person" in alert
    assert "group_adults=2" in alert
