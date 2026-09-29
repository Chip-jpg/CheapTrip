"""The engine as a controllable service (B37): search now, pause, status, events, cycle history."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import aiosqlite
import pytest

import control
from notifier.telegram import TelegramNotifier
from preferences import get_preferences
from scheduler.engine import Engine
from scrapers.aggregator import ScraperAggregator
from storage.database import (
    count_cycles_since,
    get_db_path,
    get_digest_deals,
    get_pending_instant_alerts,
    hide_deal,
    init_db,
    mark_alerted,
    recent_cycles,
)
from storage.models import AlertTier
from tests.harness import FakeFeedScraper, FakeFlightScraper, FakeHotelScraper, flight
from utils.events import get_event_bus
from utils.timeutil import utcnow


class SlowFlights(FakeFlightScraper):
    """A source that takes a while, so a search is still running when the next click arrives."""

    def __init__(self, results=(), delay: float = 0.3) -> None:
        super().__init__(results)
        self.delay = delay

    async def scrape(self, tasks):
        await asyncio.sleep(self.delay)
        return await super().scrape(tasks)


def _aggregator(*flight_scrapers) -> ScraperAggregator:
    return ScraperAggregator(
        flight_scrapers=[*(flight_scrapers or [FakeFlightScraper()]), FakeFeedScraper()],
        hotel_scrapers=[FakeHotelScraper()],
    )


async def _engine(*flight_scrapers) -> Engine:
    await init_db()
    engine = Engine(await TelegramNotifier.create(), _aggregator(*flight_scrapers), telegram_commands=False)
    await engine.start(first_search=False)
    return engine


def _drain(queue: asyncio.Queue) -> list:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


# ── Cycle history and events ──────────────────────────────────────────────────

async def test_every_cycle_is_recorded_and_announced(engine):
    with get_event_bus().subscribe() as queue:
        await engine.run_cycle(flights=[flight(destination="KRK", price=40.0), flight(destination="PRG", price=35.0)])
        events = _drain(queue)

    types = [e["type"] for e in events]
    assert types[0] == "cycle_started" and types[1] == "cycle_planned" and types[-1] == "cycle_finished"
    assert {e["source"] for e in events if e["type"] == "source_done"} == {"fake_flights", "fake_feed", "fake_hotels"}
    finished = events[-1]
    assert (finished["fares"], finished["trips"], finished["error"]) == (2, 2, None)

    [cycle] = await recent_cycles()
    assert (cycle["status"], cycle["trigger"], cycle["fares"], cycle["new_digest"]) == ("ok", "scheduled", 2, 2)
    assert cycle["duration_s"] is not None and cycle["finished_at"]
    assert await count_cycles_since(utcnow() - timedelta(hours=1)) == 1


async def test_a_failed_cycle_is_recorded_as_failed(engine, monkeypatch):
    async def _broken(self):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(ScraperAggregator, "begin_cycle", _broken)
    with get_event_bus().subscribe() as queue:
        await engine.run_cycle()
        finished = _drain(queue)[-1]

    assert finished["type"] == "cycle_finished" and finished["error"] == "planner exploded"
    [cycle] = await recent_cycles()
    assert cycle["status"] == "failed" and cycle["error"] == "planner exploded"


# ── Search now ────────────────────────────────────────────────────────────────

async def test_search_now_never_runs_two_searches_at_once(engine):
    service = await _engine(SlowFlights())
    try:
        assert await service.search_now() is True
        assert service.is_searching
        assert await service.search_now() is False  # the second click while searching

        while service.is_searching:
            await asyncio.sleep(0.05)
    finally:
        await service.stop()

    cycles = await recent_cycles()
    assert [c["trigger"] for c in cycles] == ["manual"]


async def test_starting_the_engine_searches_right_away(engine):
    await init_db()
    service = Engine(await TelegramNotifier.create(), _aggregator(), telegram_commands=False)
    await service.start()
    while service.is_searching or not await recent_cycles():
        await asyncio.sleep(0.05)
    await service.stop()

    assert (await recent_cycles())[0]["trigger"] == "startup"
    assert any("Engine started" in t for t in engine.telegram.texts)


async def test_stop_cancels_a_running_search_quickly(engine):
    service = await _engine(SlowFlights(delay=30))
    await service.search_now()
    await asyncio.sleep(0.2)  # the search is now waiting on the slow source

    await asyncio.wait_for(service.stop(), timeout=5)

    assert not service.is_running and await service.search_now() is False
    [cycle] = await recent_cycles()
    assert cycle["status"] == "stopped"  # not "ok", and not a failure that needs attention


async def test_a_search_left_running_by_a_crash_is_closed_on_start(engine):
    from storage.database import start_cycle_record

    await init_db()
    await start_cycle_record("scheduled")  # the process died mid-search

    service = await _engine()
    await service.stop()

    [cycle] = await recent_cycles()
    assert (cycle["status"], cycle["error"]) == ("stopped", "interrupted")
    assert (await service.status())["state"] == "stopped"


# ── Status ────────────────────────────────────────────────────────────────────

async def test_status_follows_the_engine(engine):
    service = await _engine(SlowFlights())
    try:
        status = await service.status()
        assert status["state"] == "running" and status["next_search_at"] and status["paused_until"] is None

        await service.pause(hours=1)
        status = await service.status()
        assert status["state"] == "paused" and status["paused_until"]

        await service.resume()
        await service.search_now()
        assert (await service.status())["state"] == "searching"
        while service.is_searching:
            await asyncio.sleep(0.05)
        status = await service.status()
        assert status["state"] == "running" and status["searches_today"] == 1
        assert status["last_cycle"]["status"] == "ok"
    finally:
        await service.stop()

    assert (await service.status())["state"] == "stopped"


async def test_a_failed_last_search_needs_attention(engine, monkeypatch):
    async def _broken(self):
        raise RuntimeError("no network")

    service = await _engine()
    monkeypatch.setattr(ScraperAggregator, "begin_cycle", _broken)
    try:
        await service.run_cycle()
        assert (await service.status())["state"] == "attention"
    finally:
        await service.stop()


# ── Controls shared with Telegram ─────────────────────────────────────────────

async def test_pause_and_resume_are_announced(engine):
    await init_db()
    with get_event_bus().subscribe() as queue:
        until = await control.pause(hours=2)
        await control.resume()
        events = _drain(queue)

    assert [e["type"] for e in events] == ["paused", "resumed"]
    assert events[0]["until"] == until.isoformat()
    assert await control.paused_until() is None


@pytest.mark.parametrize("hours", [0, -1])
async def test_pause_needs_a_positive_duration(engine, hours):
    await init_db()
    with pytest.raises(ValueError):
        await control.pause(hours=hours)


async def test_mute_priority_and_budget_update_preferences_at_once(engine):
    await init_db()
    with get_event_bus().subscribe() as queue:
        await control.set_muted("KRK", True)
        await control.set_priority("JFK", True)
        await control.set_budget(300)
        changes = [e["change"] for e in _drain(queue)]

    prefs = get_preferences()
    assert "KRK" in prefs.excluded_destinations and "JFK" in prefs.priority_destinations
    assert prefs.max_trip_budget == 300
    assert changes == ["muted", "priority", "budget"]

    await control.set_muted("KRK", False)
    await control.set_budget(None)
    prefs = get_preferences()
    assert "KRK" not in prefs.excluded_destinations and prefs.max_trip_budget is None


def test_airport_codes_are_validated():
    assert control.airport_code(" krk ") == "KRK"
    assert control.airport_code("XQZ") is None and control.airport_code("") is None


# ── Hidden deals and alert channels ───────────────────────────────────────────

async def test_hidden_deals_leave_the_digest_and_the_queue(engine):
    engine.set_prefs(priority_destinations=["KRK"])
    await init_db()
    await control.pause(hours=1)  # keep the instant deal waiting in the queue
    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0), flight(destination="PRG", price=40.0)])
    [pending] = await get_pending_instant_alerts()

    assert await hide_deal(pending.hash)

    assert await get_pending_instant_alerts() == []
    assert [t.route for t in await get_digest_deals()] == ["Milan → Prague"]


async def test_sent_alerts_remember_their_channel(engine):
    await init_db()
    await mark_alerted("abc", AlertTier.INSTANT, channel="desktop,telegram")
    await mark_alerted("def", AlertTier.DIGEST)

    async with aiosqlite.connect(await get_db_path()) as db:
        rows = await (await db.execute("SELECT hash, channel FROM alerts_sent ORDER BY id")).fetchall()
    assert rows == [("abc", "desktop,telegram"), ("def", "telegram")]
