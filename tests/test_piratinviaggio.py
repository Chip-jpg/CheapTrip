"""PiratinViaggio posts read by Claude (B21): parsing, validation, caching, pipeline."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List

import aiosqlite
import httpx
import pytest
import respx

from ai_layer.extractor import FeedDealOption, amounts_in, validate_option
from scrapers import piratinviaggio
from scrapers.base import ScrapeStatus
from scrapers.piratinviaggio import FEED_URL, PiratinViaggioScraper, parse_feed, parse_post
from storage.database import get_db_path, init_db

FIXTURES = Path(__file__).parent / "fixtures"
FEED = (FIXTURES / "piratinviaggio_feed.xml").read_text()
PARIS_URL = "https://www.piratinviaggio.it/pacchetti/capodanno-parigi-colazione"
FLIGHTS_URL = "https://www.piratinviaggio.it/voli/voli-low-cost-weekend-ottobre-europa"
PARIS_HTML = (FIXTURES / "piratinviaggio_post_parigi.html").read_text()
FLIGHTS_HTML = (FIXTURES / "piratinviaggio_post_voli.html").read_text()
TODAY = date(2026, 9, 28)
HOME = ["MXP", "LIN", "BGY"]


def _option(**overrides) -> FeedDealOption:
    fields = dict(
        kind="package", origin_iata="MXP", destination_iata="CDG", price_eur=364.0, round_trip=True,
        departure_date="2026-12-31", return_date="2027-01-03",
        hotel_name="ibis Paris Brancion Parc des Expositions 15ème", travel_window=None,
    )
    fields.update(overrides)
    return FeedDealOption(**fields)


FLIGHT_OPTION = _option(kind="flight", origin_iata="MIL", destination_iata="MAD", price_eur=30.0,
                        departure_date="2026-10-31", return_date="2026-11-03", hotel_name=None)


class FakeClaude:
    """Stands in for AsyncAnthropic: messages.parse returns queued options (or raises)."""

    def __init__(self, *answers) -> None:
        self.answers: List = list(answers)
        self.calls: List[dict] = []
        self.messages = SimpleNamespace(parse=self._parse)

    async def _parse(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(stop_reason="end_turn", parsed_output=answer)


@pytest.fixture
def claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(piratinviaggio, "_make_client", lambda api_key, timeout: fake)
    monkeypatch.setattr(piratinviaggio, "_now", lambda: datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc))
    return fake


def _mock_site(router) -> None:
    router.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED))
    router.get(PARIS_URL).mock(return_value=httpx.Response(200, text=PARIS_HTML))
    router.get(FLIGHTS_URL).mock(return_value=httpx.Response(200, text=FLIGHTS_HTML))


def _scraper() -> PiratinViaggioScraper:
    scraper = PiratinViaggioScraper()
    scraper.enabled = True  # the engine fixture blanks ANTHROPIC_API_KEY
    return scraper


# ── Parsing (live-recorded fixtures) ──────────────────────────────────────────

def test_feed_items_and_deal_sections():
    items = parse_feed(FEED)

    assert len(items) == 4
    assert [i.url for i in items if i.is_deal_section] == [FLIGHTS_URL, PARIS_URL]  # not /hotel/, /crociere/
    assert items[0].published.year == 2026


def test_post_page_embeds_the_offer_table():
    post = parse_post(PARIS_HTML, PARIS_URL)

    assert post.category == "PACKAGE"
    assert post.headline_price == "Da 364€ pp"
    assert "Milano" in post.origins and "Parigi" in post.destinations
    assert post.expires == date(2027, 1, 3)
    assert "Da MILANO Malpensa" in post.text
    assert "31.12 - 03.01 - 364€" in post.text
    assert {364.0, 451.0} <= amounts_in(post.text)


def test_page_without_post_data_is_none():
    assert parse_post("<html><body>nothing</body></html>", PARIS_URL) is None


# ── Validation: nothing the model says is trusted blindly ─────────────────────

POST = parse_post(PARIS_HTML, PARIS_URL)


def test_valid_package_passes():
    valid, reason = validate_option(_option(), POST, HOME, TODAY)

    assert reason == ""
    assert (valid.origin, valid.destination, valid.price_eur) == ("MXP", "CDG", 364.0)
    assert valid.round_trip and valid.hotel_name.startswith("ibis")


def test_city_code_maps_to_the_first_home_airport():
    valid, _ = validate_option(FLIGHT_OPTION, parse_post(FLIGHTS_HTML, FLIGHTS_URL), HOME, TODAY)

    assert valid.origin == "MXP" and valid.destination == "MAD"


@pytest.mark.parametrize("overrides,reason", [
    (dict(kind="none"), "no offer"),
    (dict(origin_iata="FCO"), "not a home airport"),
    (dict(destination_iata="XQZ"), "unknown destination"),
    (dict(price_eur=350.0), "not written in the post"),
    (dict(departure_date="2026-09-01", return_date="2026-09-04"), "in the past"),
    (dict(return_date="2026-12-30"), "not after departure"),
    (dict(departure_date=None), "return date without a departure date"),
    (dict(departure_date="31/12/2026"), "malformed date"),
])
def test_invalid_answers_are_dropped(overrides, reason):
    valid, why = validate_option(_option(**overrides), POST, HOME, TODAY)

    assert valid is None
    assert reason in why


# ── Scraper: fetch, extract once, cache ───────────────────────────────────────

async def test_scraper_turns_posts_into_deals_and_skips_other_sections(engine, claude):
    await init_db()
    claude.answers = [FLIGHT_OPTION, _option()]
    with respx.mock(assert_all_called=True) as router:  # hotel and cruise pages are never fetched
        _mock_site(router)
        outcome = await _scraper().safe_scrape([])

    assert outcome.status == ScrapeStatus.OK
    flight, package = outcome.results
    assert (flight.origin, flight.destination, flight.price, flight.is_package) == ("MXP", "MAD", 30.0, False)
    assert flight.departure_date == date(2026, 10, 31) and flight.booking_url == FLIGHTS_URL
    assert package.is_package and package.package_hotel.startswith("ibis") and package.price == 364.0
    # The model saw the whole post, including the per-airport price table
    assert "31.12 - 03.01 - 364€" in claude.calls[1]["messages"][0]["content"]
    assert claude.calls[1]["output_format"] is FeedDealOption


async def test_each_post_is_sent_to_the_model_once(engine, claude):
    await init_db()
    claude.answers = [FLIGHT_OPTION, _option()]
    with respx.mock(assert_all_called=False) as router:
        _mock_site(router)
        await _scraper().safe_scrape([])
        second = await _scraper().safe_scrape([])  # no answers queued: a model call would fail

    assert len(claude.calls) == 2
    assert second.count == 2


async def test_api_errors_are_not_cached(engine, claude):
    await init_db()
    claude.answers = [RuntimeError("overloaded"), _option(), FLIGHT_OPTION]
    with respx.mock(assert_all_called=False) as router:
        _mock_site(router)
        first = await _scraper().safe_scrape([])
        second = await _scraper().safe_scrape([])

    assert first.count == 1 and first.status == ScrapeStatus.PARTIAL
    assert second.count == 2 and len(claude.calls) == 3


async def test_rejected_answers_are_cached_with_the_reason(engine, claude):
    await init_db()
    claude.answers = [_option(kind="none"), _option(price_eur=999.0)]
    with respx.mock(assert_all_called=False) as router:
        _mock_site(router)
        outcome = await _scraper().safe_scrape([])

    assert outcome.count == 0
    async with aiosqlite.connect(await get_db_path()) as db:
        statuses = dict(await (await db.execute("SELECT url, status FROM feed_extractions")).fetchall())
    assert statuses[PARIS_URL] == "rejected: price €999 is not written in the post"


async def test_model_calls_per_cycle_are_capped(engine, claude, monkeypatch):
    monkeypatch.setenv("AI_EXTRACTION_MAX_POSTS_PER_CYCLE", "1")
    from config import get_settings
    get_settings.cache_clear()
    await init_db()
    claude.answers = [FLIGHT_OPTION]
    with respx.mock(assert_all_called=False) as router:
        _mock_site(router)
        outcome = await _scraper().safe_scrape([])

    assert len(claude.calls) == 1 and outcome.count == 1


def test_disabled_without_an_api_key(engine):
    assert PiratinViaggioScraper().enabled is False


# ── Whole pipeline ────────────────────────────────────────────────────────────

async def test_package_alert_end_to_end(engine, claude):
    engine.set_prefs(priority_destinations=["CDG"])  # packages alert instantly only for priorities
    claude.answers = [_option(kind="none"), _option()]
    _mock_site(engine.router)

    await engine.run_cycle(extra_flight_scrapers=[_scraper()])

    [alert] = [t for t in engine.telegram.texts if "PACKAGE DEAL" in t]
    assert "Milan → Paris" in alert
    assert "€364</b> per person · flight + 3 nights" in alert
    assert "ibis Paris Brancion" in alert
    assert PARIS_URL in alert
    async with aiosqlite.connect(await get_db_path()) as db:
        (recorded,) = await (await db.execute("SELECT COUNT(*) FROM price_history")).fetchone()
    assert recorded == 0, "package prices never enter the flight price history"
