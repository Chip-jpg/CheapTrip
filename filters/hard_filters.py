from __future__ import annotations

from typing import List, Tuple

from config import get_settings
from preferences import get_preferences
from storage.models import AlertTier, DealType, Trip
from utils import airports
from utils.logging_config import get_logger

log = get_logger(__name__)

def _is_europe_trip(trip: Trip) -> bool:
    """Short/medium-haul trips use the Europe price threshold."""
    if trip.outbound_flight:
        return not airports.is_long_haul(trip.outbound_flight.destination, trip.outbound_flight.origin)
    if trip.hotel:
        dest = airports.iata_for_city(trip.hotel.location.split(",")[0])
        return bool(dest) and not airports.is_long_haul(dest)
    return False


def apply_hard_filters(trips: List[Trip]) -> Tuple[List[Trip], List[Trip]]:
    """
    Split trips into (instant_eligible, digest_eligible).

    Only truly invalid trips are discarded (zero cost, excluded destinations,
    over-budget). Everything else goes to at least DIGEST tier so the user
    sees what was found.

    Returns (instant, digest) — caller decides final send.
    """
    settings = get_settings()
    prefs = get_preferences()
    excluded = set(prefs.excluded_destinations)
    instant: List[Trip] = []
    digest: List[Trip] = []
    discarded = 0

    for trip in trips:
        if trip.total_cost_eur <= 0:
            discarded += 1
            continue

        dest = trip.outbound_flight.destination if trip.outbound_flight else ""
        if dest and dest in excluded:
            discarded += 1
            continue

        if prefs.max_trip_budget and trip.total_cost_eur > prefs.max_trip_budget:
            discarded += 1
            continue

        if (
            trip.hotel
            and not trip.hotel.meets_quality_threshold
            and (not trip.discount_pct or trip.discount_pct < 70.0)
        ):
            discarded += 1
            continue

        is_europe = _is_europe_trip(trip)
        cost = trip.total_cost_eur
        discount = trip.discount_pct or 0.0

        if not trip.is_feasible:
            trip.alert_tier = AlertTier.DIGEST
            digest.append(trip)
            continue

        passes_instant = (
            (is_europe and cost < settings.europe_trip_max_eur)
            or (not is_europe and cost < settings.longhaul_trip_max_eur)
            or (trip.deal_type == DealType.HOTEL_ONLY and discount >= settings.hotel_discount_min_pct)
            or (trip.deal_type == DealType.FLIGHT_ONLY and discount >= settings.flight_discount_min_pct)
            or trip.is_error_fare
        )

        if passes_instant:
            trip.alert_tier = AlertTier.INSTANT
            instant.append(trip)
        else:
            trip.alert_tier = AlertTier.DIGEST
            digest.append(trip)

    log.info(
        "hard_filter_results",
        instant=len(instant),
        digest=len(digest),
        discarded=discarded,
    )
    return instant, digest
