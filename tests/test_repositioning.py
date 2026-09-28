"""Repositioning via hub airports (B18): real fares, date-aligned, worth it."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pytest

from preferences import UserPreferences
from scheduler.planner import SearchPlanner
from scrapers.base import SearchCapability
from storage.database import init_db
from storage.models import FlightLeg
from tests.harness import FakeFlightScraper
from trip_builder.repositioning import find_repositioning_opportunities

DEP = date(2026, 11, 20)
RET = date(2026, 11, 30)
HOME, HUBS = ["MXP", "LIN", "BGY"], ["LHR", "LGW", "AMS"]


def _leg(origin: str, dest: str, price: float, dep: date = DEP, ret: Optional[date] = RET,
         dep_time: Optional[str] = None, arr_time: Optional[str] = None) -> FlightLeg:
    return FlightLeg(
        origin=origin, destination=dest, price_eur=price, departure_date=dep, return_date=ret,
        departure_time=datetime.fromisoformat(f"{dep}T{dep_time}") if dep_time else None,
        arrival_time=datetime.fromisoformat(f"{dep}T{arr_time}") if arr_time else None,
        booking_url=f"https://example.com/{origin}-{dest}", source="test", is_round_trip=True,
    )


def _pairs(*legs):
    return find_repositioning_opportunities(list(legs), primary_origins=HOME, hub_airports=HUBS)


ONWARD = _leg("LHR", "JFK", 300.0, dep_time="13:00", arr_time="16:00")


def test_positioning_the_day_before_beats_the_direct_fare():
    [(repo, onward, total)] = _pairs(
        _leg("BGY", "STN", 40.0, dep=DEP - timedelta(days=1)),  # London by city: STN counts for LHR
        ONWARD,
        _leg("MXP", "JFK", 520.0),
    )

    assert (repo.origin, repo.hub, repo.price_eur) == ("BGY", "LHR", 40.0)
    assert repo.departure_date == DEP - timedelta(days=1) and repo.booking_url.endswith("BGY-STN")
    assert onward is ONWARD and total == 340.0


def test_same_day_connection_needs_the_same_airport_and_a_long_enough_gap():
    ok = _leg("MXP", "LHR", 60.0, dep_time="07:00", arr_time="08:10")
    tight = _leg("MXP", "LHR", 55.0, dep_time="10:00", arr_time="11:30")
    other_airport = _leg("BGY", "STN", 30.0, dep_time="06:00", arr_time="07:00")
    no_times = _leg("LIN", "LHR", 50.0)

    [(repo, _, total)] = _pairs(ok, tight, other_airport, no_times, ONWARD)

    assert repo.origin == "MXP" and total == 360.0


@pytest.mark.parametrize("dep,ret", [
    (DEP - timedelta(days=3), RET),       # leaves three days early
    (DEP - timedelta(days=1), RET + timedelta(days=2)),  # comes back two days late
    (DEP - timedelta(days=1), RET - timedelta(days=1)),  # comes back before the onward return
])
def test_dates_must_line_up(dep, ret):
    assert _pairs(_leg("MXP", "LHR", 40.0, dep=dep, ret=ret), ONWARD) == []


def test_returning_the_day_after_is_fine():
    assert len(_pairs(_leg("MXP", "LHR", 40.0, dep=DEP - timedelta(days=1), ret=RET + timedelta(days=1)),
                      ONWARD)) == 1


def test_no_positioning_fare_no_trip():
    assert _pairs(ONWARD) == []


def test_not_worth_it_against_a_cheaper_direct_fare():
    # 340 via London vs 390 direct: saves €50 (13%), under both thresholds (€80 / 25%)
    assert _pairs(_leg("MXP", "LHR", 40.0, dep=DEP - timedelta(days=1)), ONWARD, _leg("MXP", "JFK", 390.0)) == []


def test_without_a_direct_fare_only_cheap_totals_count():
    positioning = _leg("MXP", "LHR", 40.0, dep=DEP - timedelta(days=1))
    assert len(_pairs(positioning, ONWARD)) == 1  # €340 ≤ 2 × €250 backstop
    assert _pairs(positioning, _leg("LHR", "JFK", 700.0)) == []


# ── Planner ───────────────────────────────────────────────────────────────────

async def _plan(engine, prefs: UserPreferences, source):
    await init_db()
    plan = await SearchPlanner(today=date(2026, 10, 1)).plan(prefs, [source])
    return plan.for_source(source.source_id)


async def test_long_haul_sources_search_from_hubs(engine):
    source = FakeFlightScraper(budget=10, long_haul=True)
    tasks = await _plan(engine, UserPreferences(repositioning_hubs=HUBS), source)

    hub_tasks = [t for t in tasks if t.origin not in HOME]
    assert len(tasks) == 10 and len(hub_tasks) == 2  # 20% of the budget
    assert {t.origin for t in hub_tasks} <= {"LHR", "AMS"}  # one airport per hub city


async def test_exact_route_sources_search_hub_to_long_haul(engine):
    source = FakeFlightScraper(capability=SearchCapability.ROUTE_DATE, budget=10, long_haul=True)
    tasks = await _plan(engine, UserPreferences(repositioning_hubs=HUBS), source)

    hub_tasks = [t for t in tasks if t.origin in ("LHR", "AMS")]
    assert hub_tasks and all(t.destination not in ("KRK", "PRG", "LHR") for t in hub_tasks)


async def test_no_hub_searches_when_repositioning_is_off_or_short_haul(engine):
    off = await _plan(engine, UserPreferences(allow_repositioning=False), FakeFlightScraper(budget=10, long_haul=True))
    short_haul = await _plan(engine, UserPreferences(), FakeFlightScraper(source_id="ryanair_like", budget=10))

    assert all(t.origin in HOME for t in off + short_haul)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _raw(origin, dest, price, days_ahead, nights, **extra):
    from tests.harness import flight

    return flight(origin=origin, destination=dest, price=price, days_ahead=days_ahead, nights=nights,
                  source="test", **extra)


async def test_repositioned_alert_end_to_end(engine):
    engine.set_prefs(priority_destinations=["JFK"], repositioning_hubs=["LHR"])

    await engine.run_cycle(flights=[
        _raw("BGY", "STN", 40.0, days_ahead=29, nights=11),  # London the day before, back the day after
        _raw("LHR", "JFK", 300.0, days_ahead=30, nights=10),
    ])

    [alert] = [t for t in engine.telegram.texts if "REPOSITIONED DEAL" in t]
    assert "Milan → London → New York" in alert
    assert "Leg 1 (to the hub):</b> BGY ⇄ LHR: €40" in alert
    assert "Book leg 1" in alert and "Book main flight" in alert
    assert "€340" in alert


async def test_repositioning_off_builds_no_hub_trips(engine):
    engine.set_prefs(priority_destinations=["JFK"], repositioning_hubs=["LHR"], allow_repositioning=False)

    await engine.run_cycle(flights=[
        _raw("BGY", "STN", 40.0, days_ahead=29, nights=11),
        _raw("LHR", "JFK", 300.0, days_ahead=30, nights=10),
    ])

    assert not any("REPOSITIONED DEAL" in t for t in engine.telegram.texts)
