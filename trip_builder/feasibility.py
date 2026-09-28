"""
Trip Feasibility Engine

Rejects impractical itineraries based on the relationship between
trip length and travel time. Pure deterministic logic — no AI.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from config import FEASIBILITY_MAX_TRAVEL_HOURS, FEASIBILITY_MIN_NIGHTS
from storage.models import Trip, TripLengthProfile
from utils.logging_config import get_logger

log = get_logger(__name__)

# ── Approximate one-way flight hours between major route pairs ─────────────────
# Symmetric: (A, B) covers travel from A→B and B→A.
# Regional fallbacks are used for any unlisted pair.

_APPROX_FLIGHT_HOURS: Dict[Tuple[str, str], float] = {
    # ── Europe internal ──────────────────────────────────────────────────────
    ("MXP", "LHR"): 2.5, ("MXP", "LGW"): 2.5, ("MXP", "STN"): 2.5, ("MXP", "LTN"): 2.5,
    ("MXP", "CDG"): 1.5, ("MXP", "ORY"): 1.5,
    ("MXP", "AMS"): 2.0, ("MXP", "FRA"): 1.5, ("MXP", "MAD"): 2.0, ("MXP", "BCN"): 1.5,
    ("MXP", "DUB"): 2.5, ("MXP", "ARN"): 3.0, ("MXP", "CPH"): 2.5, ("MXP", "OSL"): 3.0,
    ("MXP", "HEL"): 3.5, ("MXP", "VIE"): 1.5, ("MXP", "ZRH"): 1.0, ("MXP", "BRU"): 1.5,
    ("MXP", "PRG"): 1.5, ("MXP", "WAW"): 2.5, ("MXP", "BUD"): 1.5, ("MXP", "KRK"): 2.0,
    ("MXP", "LIS"): 2.5, ("MXP", "ATH"): 2.5, ("MXP", "EDI"): 2.5,
    ("MXP", "PMI"): 1.5, ("MXP", "IBZ"): 1.5, ("MXP", "TFS"): 3.5, ("MXP", "ACE"): 3.5,
    # ── Europe → North America ───────────────────────────────────────────────
    ("MXP", "JFK"): 9.5,  ("MXP", "EWR"): 9.5,  ("MXP", "LAX"): 11.5,
    ("MXP", "MIA"): 10.5, ("MXP", "ORD"): 10.0, ("MXP", "BOS"): 9.0,
    ("MXP", "YYZ"): 9.5,  ("MXP", "YVR"): 11.5,
    ("LHR", "JFK"): 7.5,  ("LHR", "EWR"): 7.5,  ("LHR", "LAX"): 10.5,
    ("CDG", "JFK"): 8.0,  ("CDG", "LAX"): 11.0,
    # ── Europe → Asia ────────────────────────────────────────────────────────
    ("MXP", "NRT"): 12.5, ("MXP", "HND"): 12.5, ("MXP", "ICN"): 12.0,
    ("MXP", "HKG"): 12.0, ("MXP", "BKK"): 11.0, ("MXP", "SIN"): 12.5,
    ("MXP", "KUL"): 12.5, ("MXP", "DXB"): 6.0,  ("MXP", "AUH"): 6.0,
    ("MXP", "DOH"): 5.5,  ("MXP", "TLV"): 3.5,  ("MXP", "CAI"): 3.5,
    # ── Europe → South America ───────────────────────────────────────────────
    ("MXP", "GRU"): 12.5, ("MXP", "EZE"): 14.0, ("MXP", "BOG"): 11.5,
    ("MXP", "LIM"): 13.5, ("MXP", "SCL"): 14.5,
    # ── Europe → Africa ──────────────────────────────────────────────────────
    ("MXP", "JNB"): 11.5, ("MXP", "CPT"): 12.5, ("MXP", "NBO"): 8.0,
    # ── Europe → Oceania ─────────────────────────────────────────────────────
    ("MXP", "SYD"): 22.0, ("MXP", "MEL"): 22.5, ("MXP", "AKL"): 24.0,
}


def estimate_travel_hours(origin: str, destination: str) -> float:
    """
    Estimate one-way flight time in hours.
    Checks direct lookup (both orderings), then falls back to regional estimate.
    """
    o, d = origin.upper(), destination.upper()
    if o == d:
        return 0.0

    # Direct lookup
    for key in [(o, d), (d, o)]:
        if key in _APPROX_FLIGHT_HOURS:
            return _APPROX_FLIGHT_HOURS[key]

    # Regional fallback based on destination zone
    _EUROPE = {
        "KRK", "WAW", "PRG", "BUD", "LIS", "ATH", "DUB", "CPH", "ARN", "HEL",
        "OSL", "VIE", "ZRH", "BRU", "EDI", "GVA", "NCE", "MRS", "OPO", "SEV",
        "PMI", "IBZ", "TFS", "ACE", "LPA", "LHR", "LGW", "STN", "LTN", "AMS",
        "CDG", "ORY", "FRA", "MAD", "BCN", "GRO", "FCO", "CIA", "MXP", "LIN",
        "BGY", "VCE", "VRN", "BLQ", "ARN", "BMA",
    }
    _NEAR_EAST = {"DXB", "AUH", "DOH", "TLV", "CAI"}
    _ASIA = {"NRT", "HND", "ICN", "HKG", "BKK", "SIN", "KUL", "CGK"}
    _AMERICAS = {"JFK", "EWR", "LAX", "MIA", "ORD", "BOS", "YYZ", "YVR", "GRU", "EZE", "BOG", "LIM", "SCL"}
    _AFRICA = {"JNB", "CPT", "NBO"}
    _OCEANIA = {"SYD", "MEL", "AKL"}

    if d in _EUROPE:
        return 2.5
    if d in _NEAR_EAST:
        return 5.5
    if d in _ASIA:
        return 12.0
    if d in _AMERICAS:
        return 10.0
    if d in _AFRICA:
        return 10.0
    if d in _OCEANIA:
        return 22.0
    return 5.0  # conservative unknown


def get_trip_profile(nights: Optional[int]) -> TripLengthProfile:
    """Infer trip length profile from number of nights."""
    if nights is None:
        return TripLengthProfile.SHORT
    if nights <= 4:
        return TripLengthProfile.WEEKEND
    if nights <= 7:
        return TripLengthProfile.SHORT
    if nights <= 14:
        return TripLengthProfile.MEDIUM
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

    profile_key = profile.value if hasattr(profile, "value") else str(profile)

    # Travel time check
    max_hours = FEASIBILITY_MAX_TRAVEL_HOURS.get(profile_key, 24.0)
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
