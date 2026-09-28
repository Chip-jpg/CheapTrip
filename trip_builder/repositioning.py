"""
Repositioning (B18): home → hub on one ticket, hub → destination on another.

Long-haul fares from a big hub (London, Amsterdam, Paris, ...) can be much
cheaper than from home. A repositioned trip pairs two *scraped* round trips:

  positioning   home → hub, out on the onward departure day or the day
                before, back on the onward return day or the day after
  onward        hub → destination (a round trip from the hub)

Rules:
- Only real fares: no estimated positioning prices.
- A same-day outbound connection needs the same airport and a known gap of
  at least REPOSITIONING_MIN_LAYOVER_HOURS between landing and departure;
  otherwise the positioning flight must arrive the day before.
- The pair is kept when it beats the cheapest direct round trip from home
  (similar dates) by REPOSITIONING_MIN_SAVING_EUR or _PCT, or, with no
  direct fare to compare, when it costs at most twice the long-haul
  backstop price.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from config import (
    REPOSITIONING_MIN_LAYOVER_HOURS,
    REPOSITIONING_MIN_SAVING_EUR,
    REPOSITIONING_MIN_SAVING_PCT,
    get_settings,
)
from storage.models import FlightLeg, RepositioningLeg
from utils import airports
from utils.airport_clusters import cluster_city_code
from utils.logging_config import get_logger

log = get_logger(__name__)

_DIRECT_DATE_TOLERANCE_DAYS = 3


def _city(code: str) -> str:
    return cluster_city_code(code) or code


def _outbound_connects(positioning: FlightLeg, onward: FlightLeg) -> bool:
    if positioning.departure_date == onward.departure_date - timedelta(days=1):
        return True  # arrive the day before
    if positioning.departure_date != onward.departure_date:
        return False
    # Same day: same airport and a known, long enough gap
    if positioning.destination != onward.origin:
        return False
    if not (positioning.arrival_time and onward.departure_time):
        return False
    gap_h = (onward.departure_time - positioning.arrival_time).total_seconds() / 3600
    return gap_h >= REPOSITIONING_MIN_LAYOVER_HOURS


def _return_connects(positioning: FlightLeg, onward: FlightLeg) -> bool:
    return positioning.return_date in (onward.return_date, onward.return_date + timedelta(days=1))


def _cheapest_direct(onward: FlightLeg, legs: Sequence[FlightLeg], home: set) -> Optional[float]:
    tolerance = timedelta(days=_DIRECT_DATE_TOLERANCE_DAYS)
    prices = [
        leg.price_eur for leg in legs
        if leg.origin in home and _city(leg.destination) == _city(onward.destination)
        and leg.return_date and leg.departure_date
        and abs(leg.departure_date - onward.departure_date) <= tolerance
    ]
    return min(prices) if prices else None


def find_repositioning_opportunities(
    flight_legs: List[FlightLeg],
    primary_origins: Optional[List[str]] = None,
    hub_airports: Optional[List[str]] = None,
) -> List[Tuple[RepositioningLeg, FlightLeg, float]]:
    """(positioning leg, onward flight, total cost) for every worthwhile hub pairing."""
    if primary_origins is None or hub_airports is None:
        from preferences import get_preferences
        from scheduler.planner import all_origins

        prefs = get_preferences()
        primary_origins = primary_origins if primary_origins is not None else all_origins(prefs)
        hub_airports = hub_airports if hub_airports is not None else prefs.repositioning_hubs

    home = {code.upper() for code in primary_origins}
    hub_cities = {_city(h.upper()) for h in hub_airports}
    home_cities = {_city(code) for code in home}
    round_trips = [
        leg for leg in flight_legs
        if leg.departure_date and leg.return_date and not leg.is_feed_deal and not leg.is_package
    ]

    # home → hub round trips, by hub city
    positioning_by_hub: Dict[str, List[FlightLeg]] = {}
    for leg in round_trips:
        if leg.origin in home and _city(leg.destination) in hub_cities:
            positioning_by_hub.setdefault(_city(leg.destination), []).append(leg)

    backstop = get_settings().longhaul_trip_max_eur * 2
    opportunities: List[Tuple[RepositioningLeg, FlightLeg, float]] = []
    for onward in round_trips:
        hub_city = _city(onward.origin)
        if hub_city not in hub_cities or _city(onward.destination) in home_cities | hub_cities:
            continue
        candidates = [
            p for p in positioning_by_hub.get(hub_city, [])
            if _outbound_connects(p, onward) and _return_connects(p, onward)
        ]
        if not candidates:
            continue
        positioning = min(candidates, key=lambda p: p.price_eur)
        total = round(positioning.price_eur + onward.price_eur, 2)

        direct = _cheapest_direct(onward, flight_legs, home)
        if direct is not None:
            saving = direct - total
            if saving < REPOSITIONING_MIN_SAVING_EUR and saving / direct < REPOSITIONING_MIN_SAVING_PCT:
                continue
        elif total > backstop:
            continue

        repo_leg = RepositioningLeg(
            origin=positioning.origin,
            hub=onward.origin,
            transport_type="flight",
            price_eur=positioning.price_eur,
            duration_hours=round(airports.estimate_flight_hours(positioning.origin, positioning.destination), 1),
            booking_url=positioning.booking_url,
            source=positioning.source,
            data_confidence_score=positioning.data_confidence_score,
            departure_date=positioning.departure_date,
            return_date=positioning.return_date,
        )
        opportunities.append((repo_leg, onward, total))
        log.debug("repo_opportunity", origin=positioning.origin, hub=onward.origin,
                  dest=onward.destination, total=total, direct=direct)

    log.info("repositioning_pairs", count=len(opportunities))
    return opportunities
