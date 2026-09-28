"""Tests for user preferences loading and defaults."""
from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from preferences import UserPreferences, load_preferences


class TestUserPreferencesDefaults:
    def test_default_home_airports(self):
        prefs = UserPreferences()
        assert "MXP" in prefs.home_airports

    def test_default_min_hotel_rating(self):
        prefs = UserPreferences()
        assert prefs.minimum_hotel_rating == 7.0

    def test_default_preferred_hotel_rating(self):
        prefs = UserPreferences()
        assert prefs.preferred_hotel_rating == 8.0

    def test_default_trip_lengths(self):
        prefs = UserPreferences()
        assert "weekend" in prefs.preferred_trip_lengths
        assert "short" in prefs.preferred_trip_lengths

    def test_default_search_window(self):
        prefs = UserPreferences()
        assert prefs.search_window_days > 0

    def test_default_allow_repositioning(self):
        prefs = UserPreferences()
        assert prefs.allow_repositioning is True

    def test_default_max_budget_is_none(self):
        prefs = UserPreferences()
        assert prefs.max_trip_budget is None


class TestLoadPreferences:
    def test_load_from_nonexistent_file_returns_defaults(self):
        prefs = load_preferences(Path("/nonexistent/path/prefs.yaml"))
        assert isinstance(prefs, UserPreferences)
        assert len(prefs.home_airports) > 0

    def test_load_valid_yaml(self):
        data = {
            "home_airports": ["LHR", "LGW"],
            "minimum_hotel_rating": 8.5,
            "search_window_days": 60,
            "preferred_trip_lengths": ["short", "medium"],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            tmp_path = Path(f.name)

        try:
            prefs = load_preferences(tmp_path)
            assert prefs.home_airports == ["LHR", "LGW"]
            assert prefs.minimum_hotel_rating == 8.5
            assert prefs.search_window_days == 60
            assert "short" in prefs.preferred_trip_lengths
        finally:
            tmp_path.unlink()

    def test_partial_yaml_uses_defaults_for_missing(self):
        data = {"home_airports": ["WAW"]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            tmp_path = Path(f.name)

        try:
            prefs = load_preferences(tmp_path)
            assert prefs.home_airports == ["WAW"]
            assert prefs.minimum_hotel_rating == 7.0  # default
        finally:
            tmp_path.unlink()

    def test_excluded_destinations_default_empty(self):
        prefs = UserPreferences()
        assert prefs.excluded_destinations == []

    def test_priority_destinations_default_empty(self):
        prefs = UserPreferences()
        assert prefs.priority_destinations == []

    def test_custom_excluded_destinations(self):
        data = {"excluded_destinations": ["DXB", "LAS"]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            tmp_path = Path(f.name)

        try:
            prefs = load_preferences(tmp_path)
            assert "DXB" in prefs.excluded_destinations
        finally:
            tmp_path.unlink()
