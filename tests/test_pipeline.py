"""End-to-end pipeline tests: fake scrapers in, captured Telegram messages out."""
from __future__ import annotations

import pytest

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


@pytest.mark.xfail(strict=True, reason="B04: digest crashes on trips loaded from the DB (use_enum_values)")
async def test_daily_digest_is_sent_after_a_cycle(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)])
    await engine.run_digest()

    assert any("DAILY TRAVEL DEALS DIGEST" in t for t in engine.telegram.texts)
