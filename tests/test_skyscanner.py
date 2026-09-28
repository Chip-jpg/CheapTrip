"""Skyscanner (RapidAPI) scraper: opt-in, per-cycle budget, 429 pause, entity data."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx
import pytest
import respx

from config import get_settings
from scrapers import skyscanner
from scrapers.skyscanner import _KNOWN_ENTITIES, SkyscannerScraper
from storage.models import ScraperParams
from utils import airports

HOST = "skyscanner50.p.rapidapi.com"
SEARCH = f"https://{HOST}/api/v1/searchFlights"


@pytest.fixture
def sky_env(tmp_path, monkeypatch):
    monkeypatch.setattr(skyscanner, "_CACHE_PATH", tmp_path / "skyscanner_cache.json")
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key")
    monkeypatch.setenv("RAPIDAPI_SKYSCANNER_HOST", HOST)
    monkeypatch.delenv("ENABLE_SKYSCANNER", raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _ready_scraper(monkeypatch, max_calls: int = 20) -> SkyscannerScraper:
    monkeypatch.setenv("ENABLE_SKYSCANNER", "true")
    monkeypatch.setenv("SKYSCANNER_MAX_CALLS_PER_CYCLE", str(max_calls))
    get_settings.cache_clear()
    scraper = SkyscannerScraper()
    scraper._probed = True
    scraper._search_endpoint = "/api/v1/searchFlights"
    return scraper


def _params(origins=("MXP",), destinations=("KRK", "PRG", "BUD", "WAW", "LIS")) -> ScraperParams:
    dep = date.today() + timedelta(days=14)
    return ScraperParams(
        origins=list(origins), destinations=list(destinations),
        departure_date_from=dep, departure_date_to=dep + timedelta(days=13),
        nights_min=2, nights_max=4,
    )


def test_disabled_by_default_even_with_a_key(sky_env):
    assert SkyscannerScraper().enabled is False


def test_enabled_when_opted_in(sky_env):
    sky_env.setenv("ENABLE_SKYSCANNER", "true")
    get_settings.cache_clear()
    assert SkyscannerScraper().enabled is True


def test_no_entity_id_is_shared_by_different_cities():
    by_id = defaultdict(set)
    for code, entity in _KNOWN_ENTITIES.items():
        by_id[entity].add(airports.city_of(code) if airports.is_known(code) else code)
    shared = {entity: cities for entity, cities in by_id.items() if len(cities) > 1}
    assert shared == {}


@pytest.mark.parametrize("code", ["TLV", "JNB", "CPT", "NBO"])
def test_wrong_hardcoded_ids_removed(code):
    # These reused Bangkok's / Taipei's IDs and returned flights to the wrong city.
    assert code not in _KNOWN_ENTITIES


async def test_calls_are_capped_per_cycle(sky_env):
    scraper = _ready_scraper(sky_env, max_calls=3)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(SEARCH).mock(return_value=httpx.Response(200, json={"data": {"itineraries": []}}))
        await scraper.scrape(_params())
        await scraper.scrape(_params())
        assert route.call_count == 3

        scraper.begin_cycle()
        await scraper.scrape(_params())
        assert route.call_count == 6


async def test_429_pauses_until_next_utc_midnight(sky_env):
    scraper = _ready_scraper(sky_env)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(SEARCH).mock(return_value=httpx.Response(429, json={"message": "quota"}))
        assert await scraper.scrape(_params()) == []
        assert route.call_count == 1  # stops at the first 429

        scraper.begin_cycle()
        assert await scraper.scrape(_params()) == []
        assert route.call_count == 1  # still paused next cycle

    tomorrow = datetime.utcnow().date() + timedelta(days=1)
    assert scraper._paused_until == datetime.combine(tomorrow, datetime.min.time())


async def test_results_are_parsed(sky_env):
    scraper = _ready_scraper(sky_env)
    body = {"data": {"itineraries": [{
        "price": {"raw": 42.5},
        "legs": [{"carriers": {"marketing": [{"name": "Ryanair"}]}}],
        "deeplink": "https://example.com/deal",
    }]}}
    with respx.mock(assert_all_called=False) as router:
        router.get(SEARCH).mock(return_value=httpx.Response(200, json=body))
        results = await scraper.scrape(_params(destinations=("KRK",)))
    assert len(results) == 1
    assert results[0].price == 42.5
    assert results[0].airline == "Ryanair"
    assert results[0].destination == "KRK"
