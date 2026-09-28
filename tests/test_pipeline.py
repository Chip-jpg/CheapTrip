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


async def test_scrapers_receive_cluster_expanded_origins(engine):
    aggregator = await engine.run_cycle(flights=[])

    fake = aggregator._flight_scrapers[0]
    assert fake.calls, "flight scraper was never called"
    assert set(fake.calls[0].origins) == {"MXP", "LIN", "BGY"}


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
    await engine.run_cycle(flights=[flight(destination="JFK", price=900.0)])

    assert len(engine.telegram.texts) == 1
    assert "FLIGHT DEAL" in engine.telegram.texts[0]
    assert "€900" in engine.telegram.texts[0]


async def test_deal_without_booking_link_is_not_instant(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=25.0, booking_url=None)])

    # Held back to the digest (LOW booking confidence): only the cycle summary goes out.
    assert len(engine.telegram.texts) == 1
    assert "Cycle Summary" in engine.telegram.texts[0]
