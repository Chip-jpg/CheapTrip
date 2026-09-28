"""Hotel sources: Google Hotels parsing (live fixture) and date guard, Booking and HolidayPirates fixes."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from bs4 import BeautifulSoup

from config import get_settings
from scrapers import google_hotels
from scrapers.base import ScrapeStatus
from scrapers.booking_com import BookingComScraper, is_waf_challenge, parse_price_text
from scrapers.google_hotels import GoogleHotelsScraper, dates_match, parse_cards
from scrapers.holiday_pirates import HolidayPiratesHotelScraper
from storage.models import SearchTask

FIXTURE = (Path(__file__).parent / "fixtures" / "google_hotels_krakow.html").read_text()


@pytest.fixture
def enabled_hotels(monkeypatch):
    monkeypatch.setenv("ENABLE_GOOGLE_HOTELS", "true")
    monkeypatch.setenv("ENABLE_BOOKING_HTML", "true")
    get_settings.cache_clear()

    async def _no_pause(_):
        return None
    monkeypatch.setattr(google_hotels, "_pause", _no_pause)
    yield
    get_settings.cache_clear()


def _task(check_in: date, nights: int) -> SearchTask:
    return SearchTask(destination="KRK", depart_from=check_in, depart_to=check_in, nights_min=nights, nights_max=nights)


# ── Google Hotels ────────────────────────────────────────────────────────────

def test_disabled_by_default():
    get_settings.cache_clear()
    assert GoogleHotelsScraper().enabled is False
    assert BookingComScraper().enabled is False


def test_parse_live_cards():
    cards = parse_cards(FIXTURE)
    assert [c.name for c in cards] == [
        "Sereno Apartments", "MEININGER Kraków Centrum",
        "Fenna Apartments 7 minut od Rynku - Standard Apartment",
        "Hotel Unicus Krakow Old Town - Destigo Hotels",
    ]
    meininger = cards[1]
    assert meininger.nightly_eur == 28.0 and meininger.total_eur == 28.0 and meininger.nights == 1
    assert meininger.rating_out_of_5 == 4.4 and meininger.review_count == 1367
    assert meininger.dates_text == "Sep 28\u2009–\u200929"


@pytest.mark.parametrize("text,check_in,check_out,expected", [
    ("Sep 28 – 29", date(2026, 9, 28), date(2026, 9, 29), True),
    ("Sep 28 – 29", date(2026, 11, 6), date(2026, 11, 9), False),
    ("Oct 30 – Nov 2", date(2026, 10, 30), date(2026, 11, 2), True),
    (None, date(2026, 9, 28), date(2026, 9, 29), False),
])
def test_dates_match(text, check_in, check_out, expected):
    assert dates_match(text, check_in, check_out) is expected


def test_results_only_for_matching_dates(enabled_hotels):
    scraper = GoogleHotelsScraper()
    matching = scraper.results_for(FIXTURE, _task(date(2026, 9, 28), 1), "Krakow", "https://x")
    assert len(matching) == 4
    meininger = matching[1]
    assert meininger.rating == 8.8           # 4.4/5 → /10
    assert meininger.review_count == 1367
    assert meininger.price_per_night == 28.0 and meininger.nights == 1
    assert meininger.location == "Krakow"
    assert scraper.results_for(FIXTURE, _task(date(2026, 11, 6), 3), "Krakow", "https://x") == []


async def test_ignored_dates_are_reported_as_blocked(enabled_hotels):
    with respx.mock() as router:
        router.get(url__startswith="https://www.google.com/travel/search").mock(
            return_value=httpx.Response(200, text=FIXTURE))
        outcome = await GoogleHotelsScraper().safe_scrape([_task(date(2026, 11, 6), 3)])
    assert outcome.status == ScrapeStatus.BLOCKED
    assert "ignored the requested dates" in outcome.error
    assert outcome.count == 0


async def test_matching_dates_via_http(enabled_hotels):
    with respx.mock() as router:
        route = router.get(url__startswith="https://www.google.com/travel/search").mock(
            return_value=httpx.Response(200, text=FIXTURE))
        outcome = await GoogleHotelsScraper().safe_scrape([_task(date(2026, 9, 28), 1)])
    assert outcome.status == ScrapeStatus.OK and outcome.count == 4
    params = dict(route.calls[0].request.url.params)
    assert params["q"] == "hotels in Krakow" and params["checkin"] == "2026-09-28" and params["checkout"] == "2026-09-29"


# ── Booking.com ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,value", [
    ("€ 1,234", 1234.0), ("€89", 89.0), ("EUR 1.234,50", 1234.5), ("€ 2 345", 2345.0), ("Price unavailable", None),
])
def test_booking_price_parsing(text, value):
    # "€ 1,234" used to parse as 1.23
    assert parse_price_text(text) == value


async def test_booking_filter_is_encoded_once_and_waf_is_blocked(enabled_hotels):
    challenge = '<html><head><script src="https://x.awswaf.com/challenge.js"></script></head></html>'
    with respx.mock() as router:
        route = router.get(url__startswith="https://www.booking.com/searchresults.html").mock(
            return_value=httpx.Response(202, text=challenge))
        outcome = await BookingComScraper().safe_scrape([_task(date(2026, 11, 6), 3)])
    assert dict(route.calls[0].request.url.params)["nflt"] == "ht_id=204"
    assert outcome.status == ScrapeStatus.BLOCKED
    assert is_waf_challenge(challenge)


# ── HolidayPirates hotels ────────────────────────────────────────────────────

def test_holiday_pirates_hotel_price_is_per_night():
    card = BeautifulSoup('<article><h3>Hotel Sol</h3><p>3 nights in Lisbon from €300</p></article>', "lxml").article
    result = HolidayPiratesHotelScraper()._parse_hotel_card(card)
    assert result.nights == 3
    assert result.price_per_night == 100.0   # package price / nights, not €300 a night
