"""Departure date slots used by the search planner."""
from __future__ import annotations

from datetime import date

from preferences import UserPreferences
from storage.models import TripLengthProfile
from trip_builder.date_discovery import (
    date_chunks,
    departure_slots,
    month_windows,
    nights_range,
    profiles_from_prefs,
)

TODAY = date(2026, 9, 28)  # a Monday


def test_weekend_slots_are_fridays():
    slots = departure_slots(TripLengthProfile.WEEKEND, TODAY, 90)
    assert slots and all(d.weekday() == 4 for d in slots)
    assert slots[0] == date(2026, 10, 9)  # first Friday at least a week out


def test_longer_trips_depart_on_saturdays():
    for profile in (TripLengthProfile.SHORT, TripLengthProfile.MEDIUM, TripLengthProfile.LONG):
        assert all(d.weekday() == 5 for d in departure_slots(profile, TODAY, 90))


def test_slots_stay_inside_the_window():
    slots = departure_slots(TripLengthProfile.WEEKEND, TODAY, 30)
    assert all(date(2026, 10, 5) <= d <= date(2026, 10, 28) for d in slots)
    assert len(slots) == 3


def test_date_chunks_cover_the_window_without_gaps():
    chunks = date_chunks(TODAY, 90)
    assert chunks[0][0] == date(2026, 10, 5)
    assert chunks[-1][1] == date(2026, 12, 27)
    for (_, end), (start, _) in zip(chunks, chunks[1:]):
        assert (start - end).days == 1


def test_month_windows_are_clipped():
    months = month_windows(TODAY, 90)
    assert months[0] == (date(2026, 10, 5), date(2026, 10, 31))
    assert months[1] == (date(2026, 11, 1), date(2026, 11, 30))
    assert months[-1] == (date(2026, 12, 1), date(2026, 12, 27))


def test_profiles_from_prefs_ignores_unknown_and_duplicates():
    prefs = UserPreferences(preferred_trip_lengths=["weekend", "bogus", "WEEKEND", "long"])
    assert profiles_from_prefs(prefs) == [TripLengthProfile.WEEKEND, TripLengthProfile.LONG]
    assert profiles_from_prefs(UserPreferences(preferred_trip_lengths=["nope"])) == [TripLengthProfile.SHORT]


def test_nights_range():
    assert nights_range(TripLengthProfile.WEEKEND) == (2, 4)
    assert nights_range(TripLengthProfile.LONG) == (14, 30)
