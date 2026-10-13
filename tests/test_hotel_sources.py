"""Hotel sources: Google Hotels stays in ts= (live fixtures), date guard and cooldown; Booking and HolidayPirates."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from bs4 import BeautifulSoup

from config import get_settings
from scrapers import google_hotels
from scrapers.base import ScrapeStatus
from scrapers.booking_com import BookingComScraper, is_waf_challenge, parse_price_text
from scrapers.google_flights import COOLDOWN_STATE_KEY, GoogleFlightsScraper
from scrapers.google_hotels import GoogleHotelsScraper, dates_match, encode_stay, parse_cards, search_url
from scrapers.holiday_pirates import HolidayPiratesHotelScraper
from storage.database import get_state, init_db, set_state
from storage.models import SearchTask
from utils import airports
from utils import protobuf as pb
from utils.timeutil import utcnow

FIXTURES = Path(__file__).parent / "fixtures"
# Default dates (tonight, Sep 28 – 29): what Google showed before stays could be set
FIXTURE = (FIXTURES / "google_hotels_krakow.html").read_text(encoding="utf-8")
# Searched with ts= for Nov 13 – 16 2026; the third card has a deal badge and no dates
DATED = (FIXTURES / "google_hotels_krakow_dated.html").read_text(encoding="utf-8")
NOV_13 = date(2026, 11, 13)


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


def _task(check_in: date, nights: int, adults: int = 1) -> SearchTask:
    return SearchTask(destination="KRK", depart_from=check_in, depart_to=check_in, nights_min=nights,
                      nights_max=nights, adults=adults)


# ── Google Hotels ────────────────────────────────────────────────────────────

def test_google_hotels_on_and_booking_off_by_default():
    get_settings.cache_clear()
    assert GoogleHotelsScraper().enabled is True
    assert BookingComScraper().enabled is False


def test_turned_off_in_settings(monkeypatch):
    monkeypatch.setenv("ENABLE_GOOGLE_HOTELS", "false")
    get_settings.cache_clear()
    scraper = GoogleHotelsScraper()
    assert scraper.enabled is False and scraper.disabled_reason == "ENABLE_GOOGLE_HOTELS=false"
    get_settings.cache_clear()


def test_stay_is_the_url_checked_live():
    # This exact URL returned Krakow prices for Nov 13 – 16 (3 nights) when B48 was checked live
    assert encode_stay("Krakow", NOV_13, date(2026, 11, 16)) == \
        "CAEaKAoKEgg6BktyYWtvdxIaEhQKBwjqDxALGA0SBwjqDxALGBAYAzICCAEqBwoFOgNFVVI"
    q = {k: v[0] for k, v in parse_qs(urlparse(search_url("Krakow", NOV_13, date(2026, 11, 16))).query).items()}
    assert q == {"q": "hotels in Krakow", "ts": encode_stay("Krakow", NOV_13, date(2026, 11, 16)),
                 "hl": "en", "curr": "EUR"}


def test_stay_encodes_place_dates_nights_guests_and_currency():
    message = pb.decode(pb.from_url_param(encode_stay("New York", date(2026, 12, 30), date(2027, 1, 4), adults=2)))
    place_and_search = pb.decode(message[3][0])
    assert pb.decode(pb.decode(place_and_search[1][0])[2][0]) == {7: [b"New York"]}
    search = pb.decode(place_and_search[2][0])
    stay = pb.decode(search[2][0])
    assert pb.decode(stay[1][0]) == {1: [2026], 2: [12], 3: [30]}
    assert pb.decode(stay[2][0]) == {1: [2027], 2: [1], 3: [4]}
    assert stay[3] == [5]                                     # nights
    assert pb.decode(search[6][0]) == {1: [2]}                # adults
    assert pb.decode(pb.decode(message[5][0])[1][0]) == {7: [b"EUR"]}


def test_parse_dated_cards_and_the_deal_badge():
    cards = parse_cards(DATED)
    assert [c.name for c in cards] == [
        "GoldStar Luxury Old Town Krakow Apartment", "Wyndham Grand Krakow Old Town", "Antique Apartments",
    ]
    goldstar, wyndham, antique = cards
    assert (goldstar.total_eur, goldstar.nights, goldstar.dates_text) == (236.0, 3, "Nov 13\u2009–\u200916")
    assert wyndham.stars == 5 and wyndham.review_count == 1078
    # "Antique Apartments GREAT DEAL 29% less than usual" in the label; no dates on the card
    assert (antique.below_usual_pct, antique.dates_text, antique.total_eur) == (29, None, 189.0)
    assert goldstar.below_usual_pct is None


def test_dated_page_keeps_the_cards_without_dates(enabled_hotels):
    results = GoogleHotelsScraper().results_for(DATED, _task(NOV_13, 3), "Krakow", "https://x")

    assert [r.name for r in results] == [
        "GoldStar Luxury Old Town Krakow Apartment", "Wyndham Grand Krakow Old Town", "Antique Apartments",
    ]
    antique = results[2]
    assert antique.price_per_night == 63.0 and antique.nights == 3     # €189 total incl. taxes / 3
    assert (antique.check_in, antique.check_out) == (NOV_13, date(2026, 11, 16))
    assert antique.rating == 9.0 and antique.review_count == 1049 and antique.location == "Krakow"


def test_dated_page_for_other_dates_gives_nothing(enabled_hotels):
    scraper = GoogleHotelsScraper()
    assert scraper.results_for(DATED, _task(NOV_13, 2), "Krakow", "https://x") == []
    assert scraper.results_for(DATED, _task(NOV_13 + timedelta(days=7), 3), "Krakow", "https://x") == []


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


async def test_stay_searched_via_http(enabled_hotels):
    with respx.mock() as router:
        route = router.get(url__startswith="https://www.google.com/travel/search").mock(
            return_value=httpx.Response(200, text=DATED))
        outcome = await GoogleHotelsScraper().safe_scrape([_task(NOV_13, 3, adults=2)])
    assert outcome.status == ScrapeStatus.OK and outcome.count == 3
    params = dict(route.calls[0].request.url.params)
    # With its country: Google reads plain "Naples", even "Naples, IT", as Naples, Florida (checked live)
    assert params["q"] == "hotels in Krakow, Poland"
    assert params["ts"] == encode_stay("Krakow, Poland", NOV_13, date(2026, 11, 16), adults=2)
    assert outcome.results[0].booking_url == search_url("Krakow, Poland", NOV_13, date(2026, 11, 16), adults=2)
    assert outcome.results[0].location == "Krakow"  # what the trip builder matches to the flight's airport


def test_hotel_searches_name_the_country():
    assert airports.city_and_country("NAP") == "Naples, Italy"
    assert airports.city_and_country("STN") == "London, United Kingdom"
    assert airports.city_and_country("JFK") == "New York, United States"
    assert airports.country_name("XXX") is None and airports.city_and_country("XXX") == "XXX"


def test_every_airport_country_has_a_name():
    import csv

    with (Path(__file__).parent.parent / "utils" / "data" / "airports.csv").open(encoding="utf-8") as f:
        countries = {row["country"] for row in csv.DictReader(f)}
    missing = sorted(c for c in countries if not airports._country_names().get(c))
    assert not missing, f"no name in utils/data/countries.csv for {missing}"


async def test_bot_check_page_cools_down_both_google_sources(enabled_hotels):
    await init_db()
    with respx.mock() as router:
        route = router.get(url__startswith="https://www.google.com/travel/search").mock(
            return_value=httpx.Response(200, text="<html>Before you continue to Google</html>"))
        outcome = await GoogleHotelsScraper().safe_scrape([_task(NOV_13, 3), _task(NOV_13, 4)])
    assert outcome.status == ScrapeStatus.BLOCKED and route.call_count == 1

    flights = GoogleFlightsScraper()
    await flights.begin_cycle()  # the next cycle: Google Flights waits too
    assert flights.enabled is False and "cooling down" in flights.disabled_reason
    assert await get_state(COOLDOWN_STATE_KEY)


async def test_hotels_skip_google_once_flights_were_blocked_this_cycle(enabled_hotels):
    await init_db()
    hotels = GoogleHotelsScraper()
    await hotels.begin_cycle()
    await set_state(COOLDOWN_STATE_KEY, (utcnow() + timedelta(hours=2)).isoformat())  # Google Flights, meanwhile
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__startswith="https://www.google.com/travel/search").mock(
            return_value=httpx.Response(200, text=DATED))
        outcome = await hotels.safe_scrape([_task(NOV_13, 3)])
    assert route.call_count == 0 and outcome.count == 0 and hotels.enabled is False


async def test_cooldown_end_keeps_hotels_turned_off_in_settings(monkeypatch):
    monkeypatch.setenv("ENABLE_GOOGLE_HOTELS", "false")
    get_settings.cache_clear()
    await init_db()
    await set_state(COOLDOWN_STATE_KEY, (utcnow() - timedelta(minutes=1)).isoformat())
    hotels = GoogleHotelsScraper()
    await hotels.begin_cycle()
    assert hotels.enabled is False and hotels.disabled_reason == "ENABLE_GOOGLE_HOTELS=false"
    get_settings.cache_clear()


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
    assert result.source == "holiday_pirates_hotels"


def test_every_source_has_its_own_id(engine):
    # The HolidayPirates hotel scraper shared the flight scraper's id, merging their health records
    from scrapers.aggregator import build_flight_scrapers, build_hotel_scrapers
    from utils.confidence import SOURCE_RELIABILITY

    ids = [s.source_id for s in [*build_flight_scrapers(), *build_hotel_scrapers()]]

    assert len(ids) == len(set(ids)), f"duplicate source ids: {sorted(i for i in ids if ids.count(i) > 1)}"
    assert set(ids) <= set(SOURCE_RELIABILITY)
