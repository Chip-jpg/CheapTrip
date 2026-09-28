"""
Flexible date discovery — the departure dates the search planner draws from.

Goal: "find the cheapest trips anywhere in the next N days", covered over
successive cycles, rather than one arbitrary date per two-week chunk.

- Weekend trips depart on Fridays (Fri → Sun), longer trips on Saturdays.
- "Anywhere" sources take 14-day departure ranges; month sources take
  calendar months clipped to the search window.
- Nothing departs sooner than a week out.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Tuple

from config import TRIP_LENGTH_NIGHTS
from preferences import UserPreferences
from storage.models import TripLengthProfile

FIRST_DEPARTURE_OFFSET_DAYS = 7
CHUNK_DAYS = 14

_FRIDAY, _SATURDAY = 4, 5
_DEPARTURE_WEEKDAY = {
    TripLengthProfile.WEEKEND: _FRIDAY,
    TripLengthProfile.SHORT: _SATURDAY,
    TripLengthProfile.MEDIUM: _SATURDAY,
    TripLengthProfile.LONG: _SATURDAY,
}


def profiles_from_prefs(prefs: UserPreferences) -> List[TripLengthProfile]:
    """Valid trip-length profiles in preference order (unknown names are ignored)."""
    profiles: List[TripLengthProfile] = []
    for name in prefs.preferred_trip_lengths:
        try:
            profile = TripLengthProfile(name.lower())
        except ValueError:
            continue
        if profile not in profiles:
            profiles.append(profile)
    return profiles or [TripLengthProfile.SHORT]


def nights_range(profile: TripLengthProfile) -> Tuple[int, int]:
    return TRIP_LENGTH_NIGHTS.get(profile.value, (3, 7))


def _window(today: date, window_days: int) -> Tuple[date, date]:
    return today + timedelta(days=FIRST_DEPARTURE_OFFSET_DAYS), today + timedelta(days=window_days)


def departure_slots(profile: TripLengthProfile, today: date, window_days: int) -> List[date]:
    """Departure dates for exact-date searches: Fridays for weekends, Saturdays otherwise."""
    start, end = _window(today, window_days)
    weekday = _DEPARTURE_WEEKDAY[profile]
    day = start + timedelta(days=(weekday - start.weekday()) % 7)
    slots = []
    while day <= end:
        slots.append(day)
        day += timedelta(days=7)
    return slots


def date_chunks(today: date, window_days: int, chunk_days: int = CHUNK_DAYS) -> List[Tuple[date, date]]:
    """Consecutive departure ranges covering the window (for 'anywhere' searches)."""
    start, end = _window(today, window_days)
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def month_windows(today: date, window_days: int) -> List[Tuple[date, date]]:
    """Calendar months overlapping the window, clipped to it (for month-calendar searches)."""
    start, end = _window(today, window_days)
    months = []
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        months.append((cursor, min(next_month - timedelta(days=1), end)))
        cursor = next_month
    return months
