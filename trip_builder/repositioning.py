from __future__ import annotations

from typing import List, Optional, Tuple

from config import (
    LAYER_1_AIRPORTS,
    LAYER_2_AIRPORTS,
    LAYER_3_HUBS,
    REPOSITIONING_MIN_LAYOVER_HOURS,
    REPOSITIONING_MIN_SAVING_EUR,
    REPOSITIONING_MIN_SAVING_PCT,
)
from storage.models import FlightLeg, RepositioningLeg
from trip_builder.feasibility import estimate_travel_hours
from utils.logging_config import get_logger

log = get_logger(__name__)

# Approximate hub-to-hub repositioning costs (EUR) — used when no flight data available
# These are conservative estimates; replaced by actual scraped data when available
_REPO_COST_ESTIMATES: dict[Tuple[str, str], float] = {
    ("MXP", "LHR"): 60.0,
    ("MXP", "LGW"): 60.0,
    ("MXP", "AMS"): 50.0,
    ("MXP", "CDG"): 45.0,
    ("MXP", "FRA"): 55.0,
    ("MXP", "MAD"): 55.0,
    ("MXP", "BCN"): 40.0,
    ("MXP", "DUB"): 70.0,
    ("BGY", "LHR"): 55.0,
    ("BGY", "LGW"): 55.0,
    ("BGY", "AMS"): 45.0,
    ("BGY", "CDG"): 40.0,
    ("BGY", "BCN"): 35.0,
    ("BGY", "MAD"): 50.0,
    ("LIN", "LHR"): 62.0,
    ("FCO", "LHR"): 65.0,
    ("FCO", "CDG"): 50.0,
    ("VCE", "FRA"): 55.0,
    ("VCE", "AMS"): 60.0,
}


def _repo_cost(origin: str, hub: str, flight_legs: List[FlightLeg]) -> Optional[float]:
    """Find actual repositioning cost from flight legs, fall back to estimate."""
    for leg in flight_legs:
        if leg.origin == origin and leg.destination == hub:
            return leg.price_eur
    # Try reverse lookup in estimates
    key = (origin, hub)
    rev_key = (hub, origin)
    if key in _REPO_COST_ESTIMATES:
        return _REPO_COST_ESTIMATES[key]
    if rev_key in _REPO_COST_ESTIMATES:
        return _REPO_COST_ESTIMATES[rev_key]
    return None


def find_repositioning_opportunities(
    flight_legs: List[FlightLeg],
    primary_origins: List[str] = None,
    hub_airports: List[str] = None,
) -> List[Tuple[RepositioningLeg, FlightLeg, float]]:
    """
    Identify repositioning deals: primary_origin → hub → destination
    where total (repo + onward) is meaningfully cheaper than direct.

    Returns list of (repo_leg, onward_flight, total_cost).
    """
    if primary_origins is None:
        primary_origins = LAYER_1_AIRPORTS + LAYER_2_AIRPORTS
    if hub_airports is None:
        hub_airports = LAYER_3_HUBS

    # Index direct flights: (origin, dest) → cheapest leg
    direct_flights: dict[Tuple[str, str], FlightLeg] = {}
    for leg in flight_legs:
        key = (leg.origin, leg.destination)
        if key not in direct_flights or leg.price_eur < direct_flights[key].price_eur:
            direct_flights[key] = leg

    opportunities: List[Tuple[RepositioningLeg, FlightLeg, float]] = []

    for hub in hub_airports:
        # Find all onward flights departing from this hub
        hub_departures = [
            leg for leg in flight_legs
            if leg.origin == hub
        ]
        if not hub_departures:
            continue

        for origin in primary_origins:
            repo_cost = _repo_cost(origin, hub, flight_legs)
            if repo_cost is None:
                continue

            for onward in hub_departures:
                dest = onward.destination
                if dest in primary_origins or dest in hub_airports:
                    continue

                total_via_hub = repo_cost + onward.price_eur

                # Check if direct flight exists
                direct_key = (origin, dest)
                direct_cost = None
                if direct_key in direct_flights:
                    direct_cost = direct_flights[direct_key].price_eur

                # Validate repositioning rules
                if direct_cost is not None:
                    saving_eur = direct_cost - total_via_hub
                    saving_pct = saving_eur / direct_cost if direct_cost > 0 else 0
                    if saving_eur < REPOSITIONING_MIN_SAVING_EUR and saving_pct < REPOSITIONING_MIN_SAVING_PCT:
                        continue
                else:
                    # No direct comparison — only flag if total is very cheap
                    if total_via_hub > 200:
                        continue

                # Validate layover buffer: if both flights are on the same date,
                # estimated repo travel time + minimum layover must fit within the day.
                repo_travel_h = estimate_travel_hours(origin, hub)
                if repo_travel_h + REPOSITIONING_MIN_LAYOVER_HOURS > 20.0:
                    # Too tight to reliably make the connection on the same day
                    log.debug(
                        "repo_layover_insufficient",
                        origin=origin,
                        hub=hub,
                        repo_h=repo_travel_h,
                        min_layover_h=REPOSITIONING_MIN_LAYOVER_HOURS,
                    )
                    continue

                repo_leg = RepositioningLeg(
                    origin=origin,
                    hub=hub,
                    transport_type="flight",
                    price_eur=repo_cost,
                    duration_hours=2.5,  # conservative estimate
                    source="estimate" if not any(
                        l.origin == origin and l.destination == hub for l in flight_legs
                    ) else "scraped",
                    data_confidence_score=0.7 if repo_cost != _REPO_COST_ESTIMATES.get((origin, hub)) else 0.5,
                )
                opportunities.append((repo_leg, onward, total_via_hub))
                log.debug(
                    "repo_opportunity",
                    origin=origin,
                    hub=hub,
                    dest=dest,
                    total=total_via_hub,
                    direct=direct_cost,
                )

    return opportunities
