"""
User Preferences System

Loads config/user_preferences.yaml at startup and provides a typed
UserPreferences object. Falls back to safe defaults if the file is missing.
No imports from config.py — avoids circular dependencies.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

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


@lru_cache(maxsize=1)
def get_preferences() -> UserPreferences:
    return load_preferences(preferences_path())
