"""
User Preferences System

Loads config/user_preferences.yaml at startup and provides a typed
UserPreferences object. Falls back to safe defaults if the file is missing.
Overrides set from Telegram commands (/mute, /priority, /budget — stored in
the database) are merged on top: see apply_overrides().
No imports from config.py — avoids circular dependencies.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

_DEFAULT_YAML_PATH = Path(__file__).parent / "config" / "user_preferences.yaml"


class UserPreferences(BaseModel):
    home_airports: List[str] = Field(default_factory=lambda: ["MXP", "LIN", "BGY"])
    allow_repositioning: bool = True
    max_trip_budget: Optional[float] = None  # None = no limit

    excluded_destinations: List[str] = Field(default_factory=list)

    preferred_trip_lengths: List[str] = Field(
        default_factory=lambda: ["weekend", "short", "medium"]
    )

    minimum_hotel_rating: float = 7.0
    preferred_hotel_rating: float = 8.0
    hotel_low_rating_discount_threshold: float = 70.0
    hotel_exceptionally_low_trip_cost: float = 80.0

    search_window_days: int = 90

    min_hotel_review_count: Optional[int] = None  # None = no minimum

    priority_destinations: List[str] = Field(default_factory=list)

    # Travellers per booking: searches and booking links use it; prices are shown per person
    adults: int = Field(default=1, ge=1, le=9)
    # Hubs for repositioning trips (home → hub → destination); see allow_repositioning
    repositioning_hubs: List[str] = Field(
        default_factory=lambda: ["LHR", "LGW", "AMS", "CDG", "FRA", "MAD", "BCN", "DUB"]
    )
    # Airline/site market such as "it-it" or "en-gb"; empty = derived from the home airport's country
    market: Optional[str] = None


def load_preferences(path: Path = _DEFAULT_YAML_PATH) -> UserPreferences:
    """Load preferences from YAML; return defaults if file is absent or malformed."""
    if not path.exists():
        return UserPreferences()
    try:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f) or {}
        # Only pass fields that UserPreferences knows about
        known = UserPreferences.model_fields.keys()
        filtered = {k: v for k, v in data.items() if k in known and v is not None}
        return UserPreferences(**filtered)
    except Exception:
        return UserPreferences()


def preferences_path() -> Path:
    """YAML path in use: CHEAPTRIP_PREFS_PATH if set, else config/user_preferences.yaml."""
    override = os.getenv("CHEAPTRIP_PREFS_PATH")
    return Path(override) if override else _DEFAULT_YAML_PATH


# Overrides from Telegram commands; the runner loads them from the DB every cycle
_overrides: Dict[str, Any] = {}


def apply_overrides(overrides: Dict[str, Any]) -> None:
    """
    Merge command overrides over the YAML from now on. Keys:
      muted / unmuted               destinations added to / removed from excluded_destinations
      priority_added / priority_removed   the same for priority_destinations
      max_trip_budget               a number, or "off" for no limit
    """
    global _overrides
    _overrides = dict(overrides)
    get_preferences.cache_clear()


def _adjusted(base: List[str], added: List[str], removed: List[str]) -> List[str]:
    out = [code for code in base if code not in removed]
    return out + [code for code in added if code not in out]


def merge_overrides(prefs: UserPreferences, overrides: Dict[str, Any]) -> UserPreferences:
    if not overrides:
        return prefs
    update: Dict[str, Any] = {
        "excluded_destinations": _adjusted(
            prefs.excluded_destinations, overrides.get("muted", []), overrides.get("unmuted", [])
        ),
        "priority_destinations": _adjusted(
            prefs.priority_destinations, overrides.get("priority_added", []), overrides.get("priority_removed", [])
        ),
    }
    if "max_trip_budget" in overrides:
        budget = overrides["max_trip_budget"]
        update["max_trip_budget"] = None if budget == "off" else float(budget)
    return prefs.model_copy(update=update)


@lru_cache(maxsize=1)
def get_preferences() -> UserPreferences:
    return merge_overrides(load_preferences(preferences_path()), _overrides)
