"""End-to-end pipeline tests: fake scrapers in, captured Telegram messages out."""
from __future__ import annotations

from tests.harness import TEST_CHAT_ID, flight


async def test_cheap_europe_flight_sends_one_instant_alert(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=25.0)])

    assert len(engine.telegram.texts) == 1
    text = engine.telegram.texts[0]
    assert "FLIGHT DEAL" in text
    assert "€25" in text
    assert "Milan → Krakow" in text
    assert engine.telegram.payloads[0]["chat_id"] == TEST_CHAT_ID


async def test_same_deal_is_not_alerted_twice(engine):
    await engine.run_cycle(flights=[flight(price=25.0)])
    await engine.run_cycle(flights=[flight(price=25.0)])

    assert len(engine.telegram.texts) == 1


async def test_expensive_flight_only_appears_in_cycle_summary(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)])

    assert len(engine.telegram.texts) == 1
    summary = engine.telegram.texts[0]
    assert "Cycle Summary" in summary
    assert "€300" in summary


async def test_no_results_sends_nothing(engine):
    await engine.run_cycle(flights=[])

    assert engine.telegram.texts == []


async def test_anywhere_sources_search_every_home_airport(engine):
    aggregator = await engine.run_cycle(flights=[])

    fake = aggregator._flight_scrapers[0]
    assert len(fake.calls) == 1, "search sources run once per cycle with their planned tasks"
    assert {t.origin for t in fake.calls[0]} == {"MXP", "LIN", "BGY"}
    assert all(t.destination is None for t in fake.calls[0])


async def test_excluded_destination_is_never_alerted(engine):
    engine.set_prefs(excluded_destinations=["KRK"])
    await engine.run_cycle(flights=[flight(destination="KRK", price=25.0)])

    assert engine.telegram.texts == []


async def test_daily_digest_is_sent_after_a_cycle(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)])
    await engine.run_digest()

    digests = [t for t in engine.telegram.texts if "DAILY TRAVEL DEALS DIGEST" in t]
    assert len(digests) == 1
    assert "Milan → Krakow" in digests[0]


async def test_digest_lists_each_route_once_with_its_cheapest_deal(engine):
    await engine.run_cycle(flights=[
        flight(destination="KRK", price=300.0, days_ahead=10),
        flight(destination="KRK", price=280.0, days_ahead=12),
        flight(destination="PRG", price=310.0, days_ahead=10),
    ])
    await engine.run_digest()

    digest = next(t for t in engine.telegram.texts if "DAILY TRAVEL DEALS DIGEST" in t)
    assert digest.count("Milan → Krakow") == 1
    assert "€280" in digest
    assert "€300" not in digest
    assert "Milan → Prague" in digest


async def test_only_one_digest_per_day(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)])
    await engine.run_digest()
    await engine.run_digest()

    assert sum("DAILY TRAVEL DEALS DIGEST" in t for t in engine.telegram.texts) == 1


async def test_priority_destination_alerts_instantly_despite_price(engine):
    engine.set_prefs(priority_destinations=["JFK"])
    # 10 nights = MEDIUM profile (16h cap); a 3-night weekend to New York would be held back
    await engine.run_cycle(flights=[flight(destination="JFK", price=900.0, nights=10)])

    assert len(engine.telegram.texts) == 1
    assert "FLIGHT DEAL" in engine.telegram.texts[0]
    assert "€900" in engine.telegram.texts[0]


async def test_deal_without_booking_link_is_not_instant(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=25.0, booking_url=None)])

    # Held back to the digest (LOW booking confidence): only the cycle summary goes out.
    assert len(engine.telegram.texts) == 1
    assert "Cycle Summary" in engine.telegram.texts[0]


async def test_ordinary_cheap_fares_without_history_go_to_the_digest(engine):
    # Round 1 dry run: a flat €120 cap made 663 of 706 Ryanair fares instant
    await engine.run_cycle(flights=[
        flight(destination="KRK", price=35.0), flight(destination="PRG", price=30.0),
        flight(destination="BUD", price=40.0),
    ])

    assert not any("FLIGHT DEAL" in t for t in engine.telegram.texts)
    assert "Cycle Summary" in engine.telegram.texts[0]


def _same_month_days_ahead(count: int) -> list:
    """days_ahead values whose departures all fall in one month (month after next, from the 5th)."""
    from datetime import date, timedelta

    today = date.today()
    target = (today.replace(day=1) + timedelta(days=62)).replace(day=5)
    return [(target - today).days + i for i in range(count)]


async def test_price_drop_against_earlier_cycles_alerts_instantly(engine):
    days = _same_month_days_ahead(4)
    for _ in range(3):
        await engine.run_cycle(flights=[
            flight(destination="KRK", price=price, days_ahead=d) for price, d in zip((58.0, 60.0, 62.0), days)
        ])
    sent_before = len(engine.telegram.texts)

    await engine.run_cycle(flights=[flight(destination="KRK", price=35.0, days_ahead=days[3])])

    alerts = [t for t in engine.telegram.texts[sent_before:] if "FLIGHT DEAL" in t]
    assert len(alerts) == 1
    assert "€35" in alerts[0] and "below the usual €60" in alerts[0]
