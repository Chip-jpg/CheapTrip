from __future__ import annotations

from typing import List, Tuple

from config import get_settings
from filters.alert_policy import apply_decision, decide_tier
from preferences import get_preferences
from storage.models import AlertTier, Trip
from utils.logging_config import get_logger

log = get_logger(__name__)


def apply_hard_filters(trips: List[Trip]) -> Tuple[List[Trip], List[Trip]]:
    """
    Split trips into (instant_eligible, digest_eligible).

    Only truly invalid trips are discarded (zero cost, excluded destinations,
    over-budget, low-quality hotel without a big discount). Everything else
    goes to at least DIGEST so the user sees what was found; the tier itself
    is decided by filters.alert_policy.decide_tier.
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
            and (trip.discount_pct or 0.0) < prefs.hotel_low_rating_discount_threshold
        ):
            discarded += 1
            continue

        decision = decide_tier(trip, settings, prefs)
        apply_decision(trip, decision)
        if decision.tier == AlertTier.INSTANT:
            instant.append(trip)
        else:
            digest.append(trip)

    log.info(
        "hard_filter_results",
        instant=len(instant),
        digest=len(digest),
        discarded=discarded,
    )
    return instant, digest
