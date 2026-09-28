"""
Search planner — decides what each source searches this cycle.

Every source gets a call budget per cycle (its calls_per_cycle()). The
planner builds the full "universe" of searches that fit the source's
capability, then takes the next slice of it using a cursor stored in the
database, so successive cycles (and restarts) walk through every
destination and date instead of re-searching the same few.

Priority destinations are searched first every cycle, from their own
rotation, using up to half of a source's budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence

from config import POPULAR_DESTINATIONS
from preferences import UserPreferences
from scrapers.base import SearchCapability
from storage.database import get_search_cursor, set_search_cursor
from storage.models import SearchTask, TripLengthProfile
from trip_builder.date_discovery import (
    date_chunks,
    departure_slots,
    month_windows,
    nights_range,
    profiles_from_prefs,
)
from utils.airport_clusters import expand_list_to_clusters, get_cluster_name
from utils.logging_config import get_logger

log = get_logger(__name__)

# Share of a source's budget reserved for priority destinations each cycle.
PRIORITY_SHARE = 0.5
# Hotel searches use the shorter trip profiles (B15 will derive them from flight results).
_HOTEL_PROFILES = (TripLengthProfile.WEEKEND, TripLengthProfile.SHORT)


@dataclass
class CyclePlan:
    tasks: Dict[str, List[SearchTask]] = field(default_factory=dict)

    def for_source(self, source_id: str) -> List[SearchTask]:
        return self.tasks.get(source_id, [])

    @property
    def total(self) -> int:
        return sum(len(t) for t in self.tasks.values())


def origin_representatives(prefs: UserPreferences) -> List[str]:
    """One origin per home city (exact-route sources search a city once, not per airport)."""
    reps: List[str] = []
    seen_cities = set()
    for code in prefs.home_airports:
        code = code.upper()
        city = get_cluster_name(code) or code
        if city not in seen_cities:
            seen_cities.add(city)
            reps.append(code)
    return reps


def all_origins(prefs: UserPreferences) -> List[str]:
    """Every home airport incl. cluster mates ('anywhere' sources search per airport)."""
    return expand_list_to_clusters([c.upper() for c in prefs.home_airports])


def destinations(prefs: UserPreferences) -> List[str]:
    """Priority destinations first, then the popular list; excluded and home airports removed."""
    home = set(all_origins(prefs))
    excluded = {c.upper() for c in prefs.excluded_destinations}
    ordered: List[str] = []
    for code in [*prefs.priority_destinations, *POPULAR_DESTINATIONS]:
        code = code.upper()
        if code not in excluded and code not in home and code not in ordered:
            ordered.append(code)
    return ordered


class SearchPlanner:
    def __init__(self, today: Optional[date] = None) -> None:
        self._today = today

    @property
    def today(self) -> date:
        return self._today or date.today()

    # ── Universes (all searches a source could make, in rotation order) ─────────

    def _route_date_universe(self, prefs: UserPreferences, dests: Sequence[str]) -> List[SearchTask]:
        """Nearest dates first; destinations vary fastest so each cycle spreads across cities."""
        profiles = profiles_from_prefs(prefs)
        slots = {p: departure_slots(p, self.today, prefs.search_window_days) for p in profiles}
        tasks = []
        for i in range(max((len(v) for v in slots.values()), default=0)):
            for profile in profiles:
                if i >= len(slots[profile]):
                    continue
                dep = slots[profile][i]
                nights = nights_range(profile)[0]
                for dest in dests:
                    for origin in origin_representatives(prefs):
                        tasks.append(SearchTask(
                            origin=origin, destination=dest, depart_from=dep, depart_to=dep,
                            nights_min=nights, nights_max=nights, profile=profile,
                        ))
        return tasks

    def _route_month_universe(self, prefs: UserPreferences, dests: Sequence[str]) -> List[SearchTask]:
        profiles = profiles_from_prefs(prefs)
        n_min = min(nights_range(p)[0] for p in profiles)
        n_max = max(nights_range(p)[1] for p in profiles)
        tasks = []
        for start, end in month_windows(self.today, prefs.search_window_days):
            for dest in dests:
                for origin in origin_representatives(prefs):
                    tasks.append(SearchTask(
                        origin=origin, destination=dest, depart_from=start, depart_to=end,
                        nights_min=n_min, nights_max=n_max,
                    ))
        return tasks

    def _anywhere_universe(self, prefs: UserPreferences) -> List[SearchTask]:
        tasks = []
        for start, end in date_chunks(self.today, prefs.search_window_days):
            for profile in profiles_from_prefs(prefs):
                n_min, n_max = nights_range(profile)
                for origin in all_origins(prefs):
                    tasks.append(SearchTask(
                        origin=origin, destination=None, depart_from=start, depart_to=end,
                        nights_min=n_min, nights_max=n_max, profile=profile,
                    ))
        return tasks

    def _hotel_universe(self, prefs: UserPreferences, dests: Sequence[str]) -> List[SearchTask]:
        profiles = [p for p in profiles_from_prefs(prefs) if p in _HOTEL_PROFILES] or [TripLengthProfile.SHORT]
        tasks = []
        slots = {p: departure_slots(p, self.today, prefs.search_window_days) for p in profiles}
        for i in range(max((len(v) for v in slots.values()), default=0)):
            for profile in profiles:
                if i >= len(slots[profile]):
                    continue
                check_in = slots[profile][i]
                nights = nights_range(profile)[0]
                for dest in dests:
                    tasks.append(SearchTask(
                        destination=dest, depart_from=check_in, depart_to=check_in,
                        nights_min=nights, nights_max=nights, profile=profile,
                    ))
        return tasks

    def _universe(
        self, capability: SearchCapability, prefs: UserPreferences, dests: Sequence[str]
    ) -> List[SearchTask]:
        if capability == SearchCapability.ROUTE_DATE:
            return self._route_date_universe(prefs, dests)
        if capability == SearchCapability.ROUTE_MONTH:
            return self._route_month_universe(prefs, dests)
        if capability == SearchCapability.ANYWHERE:
            return self._anywhere_universe(prefs)
        if capability == SearchCapability.CITY_DATES:
            return self._hotel_universe(prefs, dests)
        return []

    # ── Rotation ──────────────────────────────────────────────────────────────

    async def _take(self, key: str, universe: List[SearchTask], n: int) -> List[SearchTask]:
        if n <= 0 or not universe:
            return []
        start = await get_search_cursor(key) % len(universe)
        picked = [universe[(start + i) % len(universe)] for i in range(min(n, len(universe)))]
        await set_search_cursor(key, (start + len(picked)) % len(universe))
        return picked

    async def plan(self, prefs: UserPreferences, sources: Iterable) -> CyclePlan:
        """Build this cycle's tasks for every enabled, non-feed source."""
        plan = CyclePlan()
        all_dests = destinations(prefs)
        priority = [d for d in all_dests if d in {p.upper() for p in prefs.priority_destinations}]
        regular = [d for d in all_dests if d not in priority]

        for source in sources:
            if not source.enabled or source.capability == SearchCapability.FEED:
                continue
            budget = max(0, source.calls_per_cycle())
            capability = source.capability
            tasks: List[SearchTask] = []

            if capability == SearchCapability.ANYWHERE:
                # One 'anywhere' search covers every destination, priority ones included.
                tasks = await self._take(f"{source.source_id}:main", self._anywhere_universe(prefs), budget)
            else:
                if priority and budget:
                    prio_universe = [
                        t.model_copy(update={"priority": True})
                        for t in self._universe(capability, prefs, priority)
                    ]
                    prio_budget = min(budget, max(1, int(budget * PRIORITY_SHARE)))
                    tasks = await self._take(f"{source.source_id}:priority", prio_universe, prio_budget)
                tasks += await self._take(
                    f"{source.source_id}:main", self._universe(capability, prefs, regular), budget - len(tasks)
                )
            plan.tasks[source.source_id] = tasks

        for tasks in plan.tasks.values():
            for task in tasks:
                task.adults = prefs.adults
        log.info("search_plan", tasks={k: len(v) for k, v in plan.tasks.items()})
        return plan

    def plan_route(
        self,
        sources: Iterable,
        origin: str,
        destination: str,
        departure: date,
        nights: int,
        flexible_days: int = 30,
        adults: int = 1,
    ) -> CyclePlan:
        """Tasks for a one-off search of a single route (the `search` CLI command)."""
        plan = CyclePlan()
        for source in sources:
            if not source.enabled or source.capability == SearchCapability.FEED:
                continue
            if source.capability == SearchCapability.CITY_DATES:
                task = SearchTask(destination=destination, depart_from=departure, depart_to=departure,
                                  nights_min=nights, nights_max=nights)
            elif source.capability == SearchCapability.ROUTE_DATE:
                task = SearchTask(origin=origin, destination=destination, depart_from=departure,
                                  depart_to=departure, nights_min=nights, nights_max=nights)
            else:
                task = SearchTask(origin=origin, destination=destination, depart_from=departure,
                                  depart_to=departure + timedelta(days=flexible_days),
                                  nights_min=nights, nights_max=nights + 3)
            task.adults = adults
            plan.tasks[source.source_id] = [task]
        return plan
