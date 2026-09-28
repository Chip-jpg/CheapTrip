"""
Flexible Date Discovery

Generates multiple ScraperParams objects covering all valid departure windows
for each preferred trip length profile, within the user's search window.

Goal: "Find cheapest trips anywhere in the next 90 days"
NOT: "Find a trip for a specific date"
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List

from config import TRIP_LENGTH_NIGHTS
from preferences import UserPreferences
from storage.models import ScraperParams, TripLengthProfile

# Chunk size: each ScraperParams covers this many departure days.
# Smaller = more API calls but finer-grained; 14 is a good default.
_CHUNK_DAYS = 14

# Maximum number of param chunks to generate (cap to prevent explosion).
_MAX_CHUNKS = 25


def generate_search_windows(
    prefs: UserPreferences,
    origins: List[str],
    destinations: List[str],
    adults: int = 1,
    max_price_eur: float = 2000.0,
) -> List[ScraperParams]:
    """
    Return a list of concrete ScraperParams, one per (profile × date-chunk).

    For WEEKEND profile: prioritizes Friday departures within each chunk.
    For all profiles: departure windows cover today+7 → today+search_window_days.
    """
    params_list: List[ScraperParams] = []
    today = date.today()
    window_end = today + timedelta(days=prefs.search_window_days)

    for profile_str in prefs.preferred_trip_lengths:
        # Normalize to enum
        try:
            profile = TripLengthProfile(profile_str.lower())
        except ValueError:
            continue

        nights_min, nights_max = TRIP_LENGTH_NIGHTS.get(profile.value, (3, 7))

        # Walk through the search window in chunks
        cursor = today + timedelta(days=7)  # never depart today
        chunks_generated = 0

        while cursor < window_end and chunks_generated < _MAX_CHUNKS:
            chunk_end = min(cursor + timedelta(days=_CHUNK_DAYS - 1), window_end)

            params_list.append(
                ScraperParams(
                    origins=origins,
                    destinations=destinations,
                    departure_date_from=cursor,
                    departure_date_to=chunk_end,
                    nights_min=nights_min,
                    nights_max=nights_max,
                    adults=adults,
                    max_price_eur=max_price_eur,
                    trip_length_profile=profile,
                    flexible_dates=True,
                )
            )
            cursor = chunk_end + timedelta(days=1)
            chunks_generated += 1

    # If no valid profiles, fall back to a single default window
    if not params_list:
        dep = today + timedelta(days=7)
        params_list.append(
            ScraperParams(
                origins=origins,
                destinations=destinations,
                departure_date_from=dep,
                departure_date_to=dep + timedelta(days=30),
                nights_min=3,
                nights_max=7,
                adults=adults,
                max_price_eur=max_price_eur,
                flexible_dates=True,
            )
        )

    return params_list
