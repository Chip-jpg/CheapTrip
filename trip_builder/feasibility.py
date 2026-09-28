"""
Trip Feasibility Engine

Rejects impractical itineraries based on the relationship between
trip length and travel time. Pure deterministic logic — no AI.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from config import FEASIBILITY_MAX_TRAVEL_HOURS, FEASIBILITY_MIN_NIGHTS, TRIP_LENGTH_NIGHTS
from storage.models import Trip, TripLengthProfile
from utils import airports
from utils.logging_config import get_logger

log = get_logger(__name__)


def estimate_travel_hours(origin: str, destination: str) -> float:
    """Estimated one-way flight time in hours (see utils.airports.estimate_flight_hours)."""
    return airports.estimate_flight_hours(origin, destination)


def get_trip_profile(nights: Optional[int]) -> Optional[TripLengthProfile]:
    """
    Trip length profile for a number of nights: the shortest profile whose
    TRIP_LENGTH_NIGHTS range reaches it (the same ranges the planner searches).
    None when the length is unknown (one-way fares, deal-feed posts).
    """
    if nights is None:
        return None
    for profile in TripLengthProfile:
        if nights <= TRIP_LENGTH_NIGHTS[profile.value][1]:
            return profile
    return TripLengthProfile.LONG


def check_feasibility(
    trip: Trip,
    profile: Optional[TripLengthProfile] = None,
) -> Tuple[bool, List[str]]:
    """
    Returns (is_feasible, notes).

    Rules:
    - WEEKEND: max 8h travel each way
    - SHORT: max 12h travel each way
    - MEDIUM: max 16h travel each way
    - LONG: max 24h travel each way + minimum 5 nights stay
    """
    notes: List[str] = []

    if profile is None:
        profile = get_trip_profile(trip.nights)

    if profile is None:
        # Unknown length: only the most lenient travel-time cap applies
        profile_key = "any-length"
        max_hours = max(FEASIBILITY_MAX_TRAVEL_HOURS.values())
    else:
        profile_key = profile.value if hasattr(profile, "value") else str(profile)
        max_hours = FEASIBILITY_MAX_TRAVEL_HOURS.get(profile_key, 24.0)

    # Travel time check
    if trip.outbound_flight:
        travel_hours = estimate_travel_hours(
            trip.outbound_flight.origin, trip.outbound_flight.destination
        )
        if travel_hours > max_hours:
            notes.append(
                f"{profile_key.upper()} trips: max {max_hours:.0f}h travel; "
                f"{trip.outbound_flight.origin}→{trip.outbound_flight.destination} "
                f"≈ {travel_hours:.0f}h"
            )

    # Minimum stay check
    min_nights = FEASIBILITY_MIN_NIGHTS.get(profile_key, 0)
    if min_nights > 0 and trip.nights is not None and trip.nights < min_nights:
        notes.append(
            f"{profile_key.upper()} trips require ≥ {min_nights} nights; "
            f"this trip has {trip.nights}"
        )

    # Repositioning sanity: repositioning cost > 60% of total means probably not worth it
    if trip.repositioning_cost_eur > 0 and trip.total_cost_eur > 0:
        repo_ratio = trip.repositioning_cost_eur / trip.total_cost_eur
        if repo_ratio > 0.60:
            notes.append(
                f"Repositioning cost (€{trip.repositioning_cost_eur:.0f}) exceeds "
                f"60% of total trip cost (€{trip.total_cost_eur:.0f})"
            )

    return len(notes) == 0, notes
