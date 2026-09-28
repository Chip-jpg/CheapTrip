"""Tests for flexible date discovery / search window generation."""
from __future__ import annotations

from datetime import date, timedelta

from preferences import UserPreferences
from trip_builder.date_discovery import _CHUNK_DAYS, _MAX_CHUNKS, generate_search_windows


def _prefs(**kwargs) -> UserPreferences:
    defaults = dict(
        home_airports=["MXP"],
        preferred_trip_lengths=["weekend"],
        search_window_days=30,
    )
    defaults.update(kwargs)
    return UserPreferences(**defaults)


class TestGenerateSearchWindows:
    def test_returns_list_of_scraper_params(self):
        prefs = _prefs()
        results = generate_search_windows(prefs, ["MXP"], ["KRK", "WAW"])
        assert len(results) > 0

    def test_departure_dates_after_today_plus_7(self):
        prefs = _prefs()
        results = generate_search_windows(prefs, ["MXP"], ["KRK"])
        today = date.today()
        min_dep = today + timedelta(days=7)
        for p in results:
            assert p.departure_date_from >= min_dep

    def test_covers_full_window(self):
        prefs = _prefs(search_window_days=60)
        results = generate_search_windows(prefs, ["MXP"], ["KRK"])
        today = date.today()
        window_end = today + timedelta(days=60)
        # Last chunk should reach (or be close to) window end
        last_end = max(p.departure_date_to for p in results)
        assert last_end >= window_end - timedelta(days=_CHUNK_DAYS)

    def test_weekend_profile_short_nights(self):
        prefs = _prefs(preferred_trip_lengths=["weekend"])
        results = generate_search_windows(prefs, ["MXP"], ["KRK"])
        for p in results:
            assert p.nights_min <= 4
            assert p.nights_max <= 4

    def test_long_profile_longer_nights(self):
        prefs = _prefs(preferred_trip_lengths=["long"])
        results = generate_search_windows(prefs, ["MXP"], ["KRK"])
        for p in results:
            assert p.nights_min >= 14

    def test_multiple_profiles_generate_more_params(self):
        prefs_one = _prefs(preferred_trip_lengths=["weekend"])
        prefs_two = _prefs(preferred_trip_lengths=["weekend", "short"])
        one = generate_search_windows(prefs_one, ["MXP"], ["KRK"])
        two = generate_search_windows(prefs_two, ["MXP"], ["KRK"])
        assert len(two) > len(one)

    def test_chunk_count_does_not_exceed_max(self):
        prefs = _prefs(search_window_days=365, preferred_trip_lengths=["short"])
        results = generate_search_windows(prefs, ["MXP"], ["KRK"])
        assert len(results) <= _MAX_CHUNKS

    def test_fallback_when_no_valid_profiles(self):
        prefs = _prefs(preferred_trip_lengths=["invalid_profile_xyz"])
        results = generate_search_windows(prefs, ["MXP"], ["KRK"])
        assert len(results) == 1  # fallback default window

    def test_flexible_dates_set_true(self):
        prefs = _prefs()
        results = generate_search_windows(prefs, ["MXP"], ["KRK"])
        for p in results:
            assert p.flexible_dates is True

    def test_origins_and_destinations_passed_through(self):
        prefs = _prefs()
        results = generate_search_windows(prefs, ["MXP", "LIN"], ["WAW", "KRK"])
        for p in results:
            assert "MXP" in p.origins
            assert "LIN" in p.origins
