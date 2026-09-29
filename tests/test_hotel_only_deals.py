"""Hotel-only deals from PiratinViaggio hotel posts (B19)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import aiosqlite
import httpx
import pytest
import respx

from ai_layer.extractor import FeedHotelOption, validate_hotel_option
from scrapers import piratinviaggio
from scrapers.base import ScrapeStatus
from scrapers.piratinviaggio import FEED_URL, PiratinViaggioHotelScraper, parse_post
from storage.database import get_db_path, init_db
from tests.harness import flight, hotel
from tests.test_piratinviaggio import FEED, FakeClaude

FIXTURES = Path(__file__).parent / "fixtures"
HOTEL_URL = "https://www.piratinviaggio.it/hotel/cappadocia-che-sogno-questo-hotel"
HOTEL_HTML = (FIXTURES / "piratinviaggio_post_cappadocia.html").read_text(encoding="utf-8")
POST = parse_post(HOTEL_HTML, HOTEL_URL)
TODAY = date(2026, 9, 28)


def _option(**overrides) -> FeedHotelOption:
    fields = dict(
        kind="hotel", hotel_name="Arinna Cappadocia", city="Göreme", country_code="tr", price_eur=48.5,
        price_basis="per_person_per_night", nights=1, check_in=None, check_out=None,
        travel_window="October to December", rating_out_of_10=9.3, review_count=None, stars=None,
    )
    fields.update(overrides)
    return FeedHotelOption(**fields)


def _feed_hotel(location: str = "Krakow", **extra):
    return hotel(location=location, is_feed_deal=True, price_basis="per person", review_count=500, **extra)


def _alerts(engine, marker: str = "HOTEL DEAL"):
    return [t for t in engine.telegram.texts if marker in t]


# ── Validation ────────────────────────────────────────────────────────────────

def test_hotel_post_is_parsed_and_the_offer_validates():
    assert POST.category == "HOTEL" and "48,50€/persona" in POST.text

    valid, reason = validate_hotel_option(_option(), POST, TODAY)

    assert reason == ""
    assert (valid.hotel_name, valid.city, valid.country_code) == ("Arinna Cappadocia", "Göreme", "TR")
    assert (valid.price_per_night, valid.price_basis, valid.nights) == (48.5, "per person", 1)
    assert valid.travel_window == "October to December" and valid.rating == 9.3


def test_price_for_a_stay_becomes_a_nightly_price():
    valid, _ = validate_hotel_option(_option(price_eur=97.0, price_basis="per_room_stay", nights=2), POST, TODAY)

    assert (valid.price_per_night, valid.price_basis, valid.nights) == (48.5, "per room", 2)


@pytest.mark.parametrize("overrides,reason", [
    (dict(kind="none"), "no priced hotel offer"),
    (dict(hotel_name=""), "no hotel name or city"),
    (dict(price_eur=45.0), "not written in the post"),
    (dict(price_basis="per_room_stay", nights=None), "without its length"),
    (dict(check_in="2026-09-01", check_out="2026-09-02"), "in the past"),
    (dict(check_in="2026-11-02", check_out="2026-11-01"), "not after check-in"),
    (dict(check_in="2026-11-02"), "only one of"),
])
def test_invalid_hotel_answers_are_dropped(overrides, reason):
    valid, why = validate_hotel_option(_option(**overrides), POST, TODAY)

    assert valid is None and reason in why


def test_out_of_range_rating_is_ignored():
    valid, _ = validate_hotel_option(_option(rating_out_of_10=93.0), POST, TODAY)

    assert valid.rating is None


# ── Reader ────────────────────────────────────────────────────────────────────

async def test_hotel_reader_only_reads_hotel_posts(engine, monkeypatch):
    fake = FakeClaude(_option())
    monkeypatch.setattr(piratinviaggio, "_make_client", lambda api_key, timeout: fake)
    monkeypatch.setattr(piratinviaggio, "_now", lambda: datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc))
    await init_db()
    reader = PiratinViaggioHotelScraper()
    reader.enabled = True
    with respx.mock(assert_all_called=True) as router:  # flight, package and cruise pages are never fetched
        router.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED))
        router.get(HOTEL_URL).mock(return_value=httpx.Response(200, text=HOTEL_HTML))
        outcome = await reader.safe_scrape([])

    assert outcome.status == ScrapeStatus.OK
    [result] = outcome.results
    assert result.is_feed_deal and result.source == "piratinviaggio_hotels"
    assert (result.name, result.location, result.price_per_night) == ("Arinna Cappadocia", "Göreme", 48.5)
    assert result.booking_url == HOTEL_URL
    assert fake.calls[0]["output_format"] is FeedHotelOption


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def test_hotel_only_deal_goes_to_the_digest(engine):
    await engine.run_cycle(hotels=[_feed_hotel(location="Krakow", price_per_night=35.0)])
    await engine.run_digest()

    assert _alerts(engine) == []
    [digest] = [t for t in engine.telegram.texts if "DAILY TRAVEL DEALS DIGEST" in t]
    assert "Krakow" in digest and "🏨" in digest and "example.com/hotel" in digest


async def test_hotel_in_a_priority_city_alerts_instantly(engine):
    engine.set_prefs(priority_destinations=["NAP"])

    await engine.run_cycle(hotels=[_feed_hotel(location="Naples", price_per_night=40.0, rating=9.1)])

    [alert] = _alerts(engine)
    assert "€40/night</b> per person" in alert
    assert "Rating:</b> 9.1/10 (500 reviews)" in alert
    assert "See the offer" in alert


async def test_min_review_count_filters_hotels(engine):
    engine.set_prefs(priority_destinations=["NAP"], min_hotel_review_count=1000)

    await engine.run_cycle(hotels=[_feed_hotel(location="Naples")])

    assert _alerts(engine) == []


async def test_hotel_posts_are_not_paired_with_flights(engine):
    await engine.run_cycle(
        flights=[flight(destination="KRK", price=300.0, nights=3, days_ahead=14)],
        hotels=[_feed_hotel(location="Krakow", days_ahead=14, nights=3)],
    )

    async with aiosqlite.connect(await get_db_path()) as db:
        types = sorted(r[0] for r in await (await db.execute("SELECT deal_type FROM deals")).fetchall())
    assert types == ["flight_only", "hotel_only"]  # same city and dates, but never a complete trip
