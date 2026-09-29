"""Deal-feed scrapers: parsing, error-fare flags, settings, once-per-cycle collection."""
from __future__ import annotations

from config import Settings
from scrapers.going import GoingScraper
from scrapers.holiday_pirates import HolidayPiratesFlightScraper
from scrapers.secret_flying import SecretFlyingScraper
from storage.models import RawFlightResult
from tests.harness import flight

SF_PAGE = """
<html><body>
<article>
  <h2><a href="/posts/milan-new-york-189">ERROR FARE: Milan, Italy to New York, USA for only €189 roundtrip</a></h2>
  <p>Travel dates: November – March 2027. THE deal FOR everyone, BUS from the airport.</p>
</article>
<article>
  <h2><a href="/posts/rome-bangkok">Rome, Italy to Bangkok, Thailand from €399 roundtrip</a></h2>
</article>
<article>
  <h2><a href="/posts/sale">Summer SALE: THE BEST DEALS FOR YOU</a></h2>
  <p>Up to 50% off in 2026</p>
</article>
</body></html>
"""


def test_secret_flying_parses_routes_prices_and_windows():
    deals = SecretFlyingScraper()._parse_page(SF_PAGE)
    assert [(d.origin, d.destination, d.price, d.currency) for d in deals] == [
        ("MXP", "JFK", 189.0, "EUR"),
        ("FCO", "BKK", 399.0, "EUR"),
    ]
    first = deals[0]
    assert first.departure_date is None
    assert first.travel_window == "November – March 2027"
    assert first.is_error_fare_hint is True
    assert first.booking_url == "https://www.secretflying.com/posts/milan-new-york-189"
    assert deals[1].is_error_fare_hint is False


def test_secret_flying_error_fare_category_flags_every_post():
    deals = SecretFlyingScraper()._parse_page(SF_PAGE, is_error_fare_page=True)
    assert all(d.is_error_fare_hint for d in deals)


def test_holiday_pirates_flight_cards():
    html = """
    <article><h3>Cheap flights Milan to Krakow, Poland for €25</h3><a href="/deals/krk">Deal</a></article>
    <article><h3>THE BEST HOTEL SALE FOR 2026</h3></article>
    """
    deals = HolidayPiratesFlightScraper()._extract_from_html(html)
    assert len(deals) == 1
    assert (deals[0].origin, deals[0].destination, deals[0].price) == ("MXP", "KRK", 25.0)
    assert deals[0].booking_url == "https://www.holidaypirates.com/deals/krk"


def test_going_cards_in_usd():
    html = '<article><p>BOS – MXP from $329 round trip</p><a href="/deals/1">x</a></article>'
    deals = GoingScraper()._parse_page(html)
    assert [(d.origin, d.destination, d.price, d.currency) for d in deals] == [("BOS", "MXP", 329.0, "USD")]


def test_enable_flags_are_read_from_the_env_file(tmp_path):
    # They used os.getenv, so setting them in .env had no effect outside Docker.
    env = tmp_path / ".env"
    env.write_text("ENABLE_SECRET_FLYING=true\nENABLE_GOING=true\n", encoding="utf-8")
    settings = Settings(_env_file=env)
    assert settings.enable_secret_flying is True
    assert settings.enable_going is True
    assert Settings(_env_file=None).enable_secret_flying is False


# ── Pipeline ─────────────────────────────────────────────────────────────────

def _feed_deal(**kw) -> RawFlightResult:
    base = dict(
        origin="MXP", destination="JFK", price=189.0, currency="EUR", departure_date=None,
        travel_window="November – March 2027", booking_url="https://example.com/post", source="secret_flying",
    )
    base.update(kw)
    return RawFlightResult(**base)


async def test_feeds_are_collected_once_per_cycle(engine):
    engine.set_prefs(preferred_trip_lengths=["weekend", "short"], search_window_days=40)
    aggregator = await engine.run_cycle(flights=[flight(price=300.0)], feeds=[_feed_deal()])

    search_fake, feed_fake = aggregator._flight_scrapers
    assert len(search_fake.calls) == 1   # planned tasks, one run per cycle
    assert len(search_fake.calls[0]) > 1
    assert feed_fake.calls == 1


async def test_undated_feed_deal_alert_shows_travel_window(engine):
    engine.set_prefs(priority_destinations=["KRK"])
    await engine.run_cycle(feeds=[_feed_deal(destination="KRK", price=25.0, is_error_fare_hint=False)])

    assert len(engine.telegram.texts) == 1
    text = engine.telegram.texts[0]
    assert "November – March 2027" in text
    assert "see the deal post" in text


async def test_undated_from_price_does_not_trigger_the_backstop(engine):
    # "From €25" on a feed post isn't a bookable fare: only dated trips hit the €25 backstop
    await engine.run_cycle(feeds=[_feed_deal(destination="KRK", price=25.0, is_error_fare_hint=False)])

    assert not any("FLIGHT DEAL" in t for t in engine.telegram.texts)


async def test_error_fare_hint_marks_the_trip(engine):
    await engine.run_cycle(feeds=[_feed_deal(is_error_fare_hint=True)])

    assert len(engine.telegram.texts) == 1
    assert "Possible error fare" in engine.telegram.texts[0]
