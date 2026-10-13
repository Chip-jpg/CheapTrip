"""Search planner: budgets, rotation, priority destinations, date slots, capabilities."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from config import POPULAR_DESTINATIONS
from preferences import UserPreferences
from scheduler.planner import SearchPlanner, destinations, origin_representatives
from scrapers.base import SearchCapability
from storage.database import init_db
from storage.models import FlightLeg
from storage.price_analytics import AnomalyResult

TODAY = date(2026, 9, 28)


class Source:
    def __init__(self, source_id, capability, budget, enabled=True):
        self.source_id, self.capability, self._budget, self.enabled = source_id, capability, budget, enabled

    def calls_per_cycle(self):
        return self._budget


@pytest.fixture
async def db(engine):
    await init_db()


def _prefs(**kw) -> UserPreferences:
    base = dict(home_airports=["MXP"], preferred_trip_lengths=["weekend"], search_window_days=90)
    base.update(kw)
    return UserPreferences(**base)


async def test_budget_is_respected(db):
    plan = await SearchPlanner(TODAY).plan(_prefs(), [Source("gf", SearchCapability.ROUTE_DATE, 20)])
    assert len(plan.for_source("gf")) == 20


async def test_rotation_covers_every_destination(db):
    prefs = _prefs()
    source = Source("gf", SearchCapability.ROUTE_DATE, 20)
    planner = SearchPlanner(TODAY)
    expected = set(destinations(prefs))
    seen = set()
    cycles = 0
    while seen != expected:
        cycles += 1
        plan = await planner.plan(prefs, [source])
        seen |= {t.destination for t in plan.for_source("gf")}
        assert cycles <= 5, f"not covered after {cycles} cycles: {sorted(expected - seen)}"
    # ~60 destinations at 20 per cycle
    assert cycles == -(-len(expected) // 20)


async def test_rotation_survives_restart(db):
    prefs = _prefs()
    source = Source("gf", SearchCapability.ROUTE_DATE, 10)
    first = await SearchPlanner(TODAY).plan(prefs, [source])
    second = await SearchPlanner(TODAY).plan(prefs, [source])  # a new process would do this
    assert {t.destination for t in first.for_source("gf")}.isdisjoint(
        {t.destination for t in second.for_source("gf")}
    )


async def test_priority_destinations_every_cycle(db):
    prefs = _prefs(priority_destinations=["NRT"])
    source = Source("gf", SearchCapability.ROUTE_DATE, 10)
    planner = SearchPlanner(TODAY)
    for _ in range(4):
        tasks = (await planner.plan(prefs, [source])).for_source("gf")
        assert len(tasks) == 10
        priority = [t for t in tasks if t.priority]
        assert priority and all(t.destination == "NRT" for t in priority)
        assert all(not t.priority for t in tasks if t.destination != "NRT")


async def test_weekend_route_tasks_are_friday_to_sunday(db):
    plan = await SearchPlanner(TODAY).plan(_prefs(), [Source("gf", SearchCapability.ROUTE_DATE, 30)])
    for task in plan.for_source("gf"):
        assert task.depart_from == task.depart_to
        assert task.depart_from.weekday() == 4
        assert task.return_date == task.depart_from + timedelta(days=2)


async def test_one_origin_per_home_city_for_route_sources(db):
    prefs = _prefs(home_airports=["MXP", "BGY", "FCO"])
    assert origin_representatives(prefs) == ["MXP", "FCO"]
    plan = await SearchPlanner(TODAY).plan(prefs, [Source("gf", SearchCapability.ROUTE_DATE, 40)])
    assert {t.origin for t in plan.for_source("gf")} == {"MXP", "FCO"}


async def test_anywhere_tasks_cover_every_home_airport(db):
    prefs = _prefs(preferred_trip_lengths=["weekend", "short"])
    plan = await SearchPlanner(TODAY).plan(prefs, [Source("fr", SearchCapability.ANYWHERE, 100)])
    tasks = plan.for_source("fr")
    assert {t.origin for t in tasks} == {"MXP", "LIN", "BGY"}
    assert all(t.destination is None for t in tasks)
    assert {(t.nights_min, t.nights_max) for t in tasks} == {(2, 4), (4, 7)}


async def test_month_tasks(db):
    plan = await SearchPlanner(TODAY).plan(_prefs(), [Source("tp", SearchCapability.ROUTE_MONTH, 5)])
    task = plan.for_source("tp")[0]
    assert task.depart_from == date(2026, 10, 5)
    assert task.depart_to == date(2026, 10, 31)


async def test_hotel_sources_get_no_rotation(db):
    # Hotels are planned after the flights, from them (plan_hotels)
    plan = await SearchPlanner(TODAY).plan(_prefs(), [Source("gh", SearchCapability.CITY_DATES, 5)])
    assert plan.for_source("gh") == []


DEP = TODAY + timedelta(days=20)


def _leg(dest="KRK", price=40.0, origin="MXP", dep=DEP, nights=3, **extra) -> FlightLeg:
    return FlightLeg(origin=origin, destination=dest, price_eur=price, departure_date=dep,
                     return_date=dep + timedelta(days=nights) if nights else None, source="ryanair", **extra)


class Index:
    """A price index stand-in: each destination's fare is this far from its usual price."""

    def __init__(self, deviation):
        self.deviation = deviation

    def check(self, leg):
        return AnomalyResult(deviation_pct=self.deviation.get(leg.destination))


def test_hotels_price_the_stays_of_the_flights():
    legs = [_leg("KRK", 40.0, nights=3), _leg("BCN", 30.0, nights=2)]
    plan = SearchPlanner(TODAY).plan_hotels(_prefs(adults=2), legs, [Source("gh", SearchCapability.CITY_DATES, 5)])

    tasks = plan.for_source("gh")
    assert [(t.destination, t.depart_from, t.return_date) for t in tasks] == [
        ("BCN", DEP, DEP + timedelta(days=2)), ("KRK", DEP, DEP + timedelta(days=3))]  # cheapest first
    assert all(t.origin is None and t.adults == 2 for t in tasks)


def test_hotels_go_first_to_priority_then_furthest_below_usual():
    legs = [_leg("BCN", 20.0), _leg("KRK", 60.0), _leg("NAP", 50.0), _leg("LIS", 30.0)]
    index = Index({"KRK": -45.0, "NAP": -10.0})
    plan = SearchPlanner(TODAY).plan_hotels(
        _prefs(priority_destinations=["LIS"]), legs, [Source("gh", SearchCapability.CITY_DATES, 3)], index)

    assert [t.destination for t in plan.for_source("gh")] == ["LIS", "KRK", "NAP"]  # budget 3: BCN waits
    assert plan.for_source("gh")[0].priority


def test_hotels_search_each_city_and_stay_once():
    legs = [_leg("STN", 30.0), _leg("LGW", 25.0), _leg("STN", 35.0, nights=4)]  # both London
    plan = SearchPlanner(TODAY).plan_hotels(_prefs(), legs, [Source("gh", SearchCapability.CITY_DATES, 10)])

    assert [(t.destination, t.nights_min) for t in plan.for_source("gh")] == [("LGW", 3), ("STN", 4)]


def test_no_hotels_for_one_ways_packages_muted_or_other_origins():
    legs = [_leg("KRK", nights=None), _leg("BCN", is_package=True), _leg("NAP", origin="FCO"), _leg("LIS")]
    plan = SearchPlanner(TODAY).plan_hotels(
        _prefs(excluded_destinations=["LIS"]), legs,
        [Source("gh", SearchCapability.CITY_DATES, 10), Source("off", SearchCapability.CITY_DATES, 10, enabled=False)])

    assert plan.for_source("gh") == [] and "off" not in plan.tasks


async def test_excluded_and_home_airports_are_not_destinations(db):
    prefs = _prefs(excluded_destinations=["KRK"], home_airports=["MXP"])
    dests = destinations(prefs)
    assert "KRK" not in dests
    assert not {"MXP", "LIN", "BGY"} & set(dests)
    assert set(dests) == set(POPULAR_DESTINATIONS) - {"KRK"}


async def test_disabled_feed_and_zero_budget_sources_get_nothing(db):
    prefs = _prefs(priority_destinations=["NRT"])
    plan = await SearchPlanner(TODAY).plan(prefs, [
        Source("off", SearchCapability.ROUTE_DATE, 20, enabled=False),
        Source("feed", SearchCapability.FEED, 20),
        Source("zero", SearchCapability.ROUTE_DATE, 0),
    ])
    assert plan.total == 0
