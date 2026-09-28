from __future__ import annotations

import uuid
from typing import List, Optional

from config import LAYER_1_AIRPORTS, LAYER_2_AIRPORTS, LAYER_3_HUBS
from storage.database import get_price_median, record_price
from storage.models import (
    DealType,
    FlightLeg,
    HotelDeal,
    Trip,
)
from storage.price_analytics import detect_anomaly, update_price_stats
from trip_builder.categorizer import categorize_trip
from trip_builder.cost_calculator import (
    assign_verdict,
    calculate_trip_total,
    compute_discount_pct,
    estimate_normal_price,
)
from trip_builder.feasibility import check_feasibility, get_trip_profile
from trip_builder.repositioning import find_repositioning_opportunities
from utils import airports
from utils.confidence import compute_booking_confidence, compute_trip_confidence
from utils.logging_config import get_logger

log = get_logger(__name__)

def _is_short_haul(origin: str, destination: str) -> bool:
    """Short/medium-haul trips use the Europe price thresholds."""
    return not airports.is_long_haul(destination, origin)


def _make_route_string(origin: str, dest: str, hub: Optional[str] = None) -> str:
    o = airports.city_of(origin)
    d = airports.city_of(dest)
    if hub:
        h = airports.city_of(hub)
        return f"{o} → {h} → {d}"
    return f"{o} → {d}"


def _hotel_airports(location: str) -> List[str]:
    """Airports serving a hotel's location ("Krakow" or "Krakow, Poland")."""
    codes = airports.airports_in_city(location)
    if not codes and "," in location:
        codes = airports.airports_in_city(location.split(",")[0])
    return codes


def _apply_anomaly_to_trip(trip: Trip, anomaly) -> None:
    """Decorate a trip with historical anomaly data (the alert policy uses it for the tier)."""
    if anomaly.is_anomaly:
        trip.is_anomaly = True
        trip.anomaly_description = anomaly.description
        trip.is_historical_low = anomaly.is_all_time_low
        if anomaly.deviation_pct is not None:
            trip.historical_deviation_pct = anomaly.deviation_pct


def _finalize_trip(trip: Trip) -> None:
    """
    Apply all post-build decorations in order:
    1. Booking confidence
    2. Feasibility check
    3. Deal categorization
    4. Hash
    """
    # Booking confidence
    has_url = bool(
        (trip.outbound_flight and trip.outbound_flight.booking_url)
        or (trip.hotel and trip.hotel.booking_url)
    )
    trip.booking_confidence = compute_booking_confidence(
        data_confidence_score=trip.data_confidence_score,
        source_count=len(trip.source_list),
        has_booking_url=has_url,
        is_error_fare=trip.is_error_fare,
    )

    # Feasibility
    profile = trip.trip_length_profile or get_trip_profile(trip.nights)
    feasible, notes = check_feasibility(trip, profile)
    trip.is_feasible = feasible
    trip.feasibility_notes = notes
    trip.trip_length_profile = profile

    # Deal categorization
    trip.category = categorize_trip(trip)

    # Hash
    trip.hash = trip.compute_hash()


async def build_trips(
    flight_legs: List[FlightLeg],
    hotel_deals: List[HotelDeal],
) -> List[Trip]:
    """
    Core trip assembly. Produces:
    1. Flight-only deals (direct)
    2. Complete trips (flight + hotel)
    3. Repositioned multi-leg trips
    """
    trips: List[Trip] = []

    # Record prices for historical tracking and keep price_stats fresh
    routes_seen: set[str] = set()
    for leg in flight_legs:
        if leg.departure_date is None:
            continue  # deal-feed "from" prices would skew route history
        route = f"{leg.origin}-{leg.destination}"
        await record_price(route, leg.price_eur, leg.source)
        routes_seen.add(route)
    for route in routes_seen:
        await update_price_stats(route)

    # ── 1. Direct flight-only deals ──────────────────────────────────────────
    for leg in flight_legs:
        if leg.origin not in (LAYER_1_AIRPORTS + LAYER_2_AIRPORTS + LAYER_3_HUBS):
            continue
        trip = await _build_flight_only_trip(leg)
        if trip:
            trips.append(trip)

    # ── 2. Complete trip (flight + hotel) ────────────────────────────────────
    # Index quality hotels by destination city (skip hotels below quality threshold)
    hotel_by_dest: dict[str, List[HotelDeal]] = {}
    for hotel in hotel_deals:
        if not hotel.meets_quality_threshold:
            continue
        for airport in _hotel_airports(hotel.location):
            hotel_by_dest.setdefault(airport, []).append(hotel)

    # Also index by cluster membership
    from utils.airport_clusters import expand_to_cluster
    for leg in flight_legs:
        if leg.origin not in (LAYER_1_AIRPORTS + LAYER_2_AIRPORTS):
            continue
        # Check destination and all cluster-mates
        dest_airports = expand_to_cluster(leg.destination)
        dest_hotels: List[HotelDeal] = []
        seen_hotels = set()
        for da in dest_airports:
            for h in hotel_by_dest.get(da, []):
                key = f"{h.name}_{h.location}"
                if key not in seen_hotels:
                    seen_hotels.add(key)
                    dest_hotels.append(h)

        # Sort by price and take top 3
        dest_hotels.sort(key=lambda h: h.total_price_eur)
        for hotel in dest_hotels[:3]:
            trip = await _build_complete_trip(leg, hotel)
            if trip:
                trips.append(trip)

    # ── 3. Repositioned trips ────────────────────────────────────────────────
    repo_opportunities = find_repositioning_opportunities(flight_legs)
    for repo_leg, onward_flight, total_cost in repo_opportunities:
        trip = await _build_repositioned_trip(repo_leg, onward_flight, total_cost)
        if trip:
            trips.append(trip)

    log.info("trips_built", count=len(trips))
    return trips


async def _build_flight_only_trip(leg: FlightLeg) -> Optional[Trip]:
    route = _make_route_string(leg.origin, leg.destination)
    median = await get_price_median(f"{leg.origin}-{leg.destination}")
    normal = estimate_normal_price(route, median, None, 0)
    discount = compute_discount_pct(leg.price_eur, normal)
    is_europe = _is_short_haul(leg.origin, leg.destination)

    verdict = assign_verdict(leg.price_eur, discount, leg.data_confidence_score, is_europe)

    trip = Trip(
        trip_id=str(uuid.uuid4())[:8],
        deal_type=DealType.FLIGHT_ONLY,
        route=route,
        outbound_flight=leg,
        flight_cost_eur=leg.price_eur,
        normal_price_eur=normal,
        discount_pct=discount,
        departure_date=leg.departure_date,
        return_date=leg.return_date,
        data_confidence_score=leg.data_confidence_score,
        source_list=[leg.source],
        verdict=verdict,
    )
    trip.compute_totals()

    # Flag potential error fares: flagged by the source, or a dated fare far below the
    # route median (feed "from" prices aren't comparable to dated history).
    far_below_median = bool(leg.departure_date and normal and leg.price_eur < normal * 0.40)
    if leg.is_error_fare_hint or far_below_median:
        trip.is_error_fare = True
        trip.deal_type = DealType.ERROR_FARE

    # Historical anomaly check (dated fares only)
    try:
        if leg.departure_date:
            anomaly = await detect_anomaly(f"{leg.origin}-{leg.destination}", leg.price_eur)
            if anomaly.is_anomaly:
                _apply_anomaly_to_trip(trip, anomaly)
                if anomaly.description:
                    trip.verdict = f"{trip.verdict} (Historical anomaly: {anomaly.description})"
    except Exception:
        log.warning("anomaly_check_failed", route=f"{leg.origin}-{leg.destination}")

    _finalize_trip(trip)
    return trip


async def _build_complete_trip(leg: FlightLeg, hotel: HotelDeal) -> Optional[Trip]:
    nights = hotel.nights
    route = _make_route_string(leg.origin, leg.destination)

    flight_median = await get_price_median(f"{leg.origin}-{leg.destination}")
    total_cost = calculate_trip_total(leg, None, hotel, [])
    normal = estimate_normal_price(route, flight_median, None, nights)

    discount = compute_discount_pct(total_cost, normal)
    is_europe = _is_short_haul(leg.origin, leg.destination)
    confidence = compute_trip_confidence([leg.data_confidence_score, hotel.data_confidence_score])
    verdict = assign_verdict(total_cost, discount, confidence, is_europe)

    # Skip trips with avg_historical vs total_cost if hotel doesn't meet quality threshold
    if not hotel.meets_quality_threshold:
        return None

    trip = Trip(
        trip_id=str(uuid.uuid4())[:8],
        deal_type=DealType.COMPLETE_TRIP,
        route=route,
        outbound_flight=leg,
        hotel=hotel,
        flight_cost_eur=leg.price_eur,
        hotel_cost_eur=hotel.total_price_eur,
        total_cost_eur=total_cost,
        normal_price_eur=normal,
        discount_pct=discount,
        departure_date=leg.departure_date,
        return_date=leg.return_date,
        nights=nights,
        data_confidence_score=confidence,
        source_list=list({leg.source, hotel.source}),
        verdict=verdict,
    )

    # Historical anomaly on the flight leg
    try:
        anomaly = await detect_anomaly(f"{leg.origin}-{leg.destination}", leg.price_eur)
        if anomaly.is_anomaly:
            _apply_anomaly_to_trip(trip, anomaly)
    except Exception:
        log.warning("anomaly_check_failed", route=f"{leg.origin}-{leg.destination}")

    _finalize_trip(trip)
    return trip


async def _build_repositioned_trip(
    repo_leg,
    onward_flight: FlightLeg,
    total_cost: float,
) -> Optional[Trip]:
    route = _make_route_string(repo_leg.origin, onward_flight.destination, repo_leg.hub)
    is_europe = _is_short_haul(repo_leg.origin, onward_flight.destination)

    confidence = compute_trip_confidence(
        [repo_leg.data_confidence_score, onward_flight.data_confidence_score]
    )
    verdict = assign_verdict(total_cost, None, confidence, is_europe)

    trip = Trip(
        trip_id=str(uuid.uuid4())[:8],
        deal_type=DealType.REPOSITIONED,
        route=route,
        outbound_flight=onward_flight,
        repositioning_legs=[repo_leg],
        flight_cost_eur=onward_flight.price_eur,
        repositioning_cost_eur=repo_leg.price_eur,
        total_cost_eur=total_cost,
        departure_date=onward_flight.departure_date,
        return_date=onward_flight.return_date,
        data_confidence_score=confidence,
        source_list=list({repo_leg.source, onward_flight.source}),
        verdict=verdict,
    )
    _finalize_trip(trip)
    return trip
