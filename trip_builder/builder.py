from __future__ import annotations

import uuid
from typing import List, Optional

from preferences import get_preferences
from scheduler.planner import all_origins
from storage.models import (
    DealType,
    FlightLeg,
    HotelDeal,
    Trip,
)
from storage.price_analytics import AnomalyResult, PriceIndex, record_fares
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


def _apply_anomaly_to_trip(trip: Trip, anomaly: AnomalyResult) -> None:
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
    cycle_id: Optional[str] = None,
) -> List[Trip]:
    """
    Core trip assembly. Produces:
    1. Flight-only deals (direct)
    2. Complete trips (flight + hotel)
    3. Repositioned multi-leg trips

    Fares are judged against baselines from earlier cycles only, then this
    cycle's fares are added to the price history under `cycle_id`.
    """
    trips: List[Trip] = []
    cycle_id = cycle_id or uuid.uuid4().hex[:12]
    price_index = await PriceIndex.load(exclude_cycle=cycle_id)

    # Trips start from the user's home airports (and their cluster mates);
    # legs from other airports only feed repositioning.
    home_origins = set(all_origins(get_preferences()))

    # ── 1. Direct flight-only deals ──────────────────────────────────────────
    for leg in flight_legs:
        if leg.origin not in home_origins:
            continue
        trip = _build_package_trip(leg) if leg.is_package else _build_flight_only_trip(leg, price_index)
        if trip:
            trips.append(trip)

    # ── 2. Complete trip (flight + hotel) ────────────────────────────────────
    # Index quality hotels by destination airport (skip hotels below quality threshold)
    hotel_by_dest: dict[str, List[HotelDeal]] = {}
    for hotel in hotel_deals:
        if not hotel.meets_quality_threshold or hotel.is_feed_deal:
            continue  # deal-site hotel posts become hotel-only deals below
        for airport in _hotel_airports(hotel.location):
            hotel_by_dest.setdefault(airport, []).append(hotel)

    from utils.airport_clusters import expand_to_cluster
    for leg in flight_legs:
        if leg.origin not in home_origins or leg.is_package:
            continue
        nights = leg.stay_nights
        if nights is None:
            continue  # a hotel stay needs the flight's dates
        # Hotels at the destination or a cluster mate, for exactly the flight's stay
        dest_hotels: List[HotelDeal] = []
        seen_hotels = set()
        for da in expand_to_cluster(leg.destination):
            for h in hotel_by_dest.get(da, []):
                if h.check_in != leg.departure_date or h.nights != nights:
                    continue
                key = f"{h.name}_{h.location}"
                if key not in seen_hotels:
                    seen_hotels.add(key)
                    dest_hotels.append(h)

        # Sort by price and take top 3
        dest_hotels.sort(key=lambda h: h.total_price_eur)
        for hotel in dest_hotels[:3]:
            trip = _build_complete_trip(leg, hotel, price_index)
            if trip:
                trips.append(trip)

    # ── 3. Hotel-only deals (deal-site hotel posts) ──────────────────────────
    for hotel in hotel_deals:
        if hotel.is_feed_deal:
            trips.append(_build_hotel_only_trip(hotel))

    # ── 4. Repositioned trips ────────────────────────────────────────────────
    repo_opportunities = find_repositioning_opportunities(
        [leg for leg in flight_legs if not leg.is_package], primary_origins=sorted(home_origins)
    )
    for repo_leg, onward_flight, total_cost in repo_opportunities:
        trip = await _build_repositioned_trip(repo_leg, onward_flight, total_cost)
        if trip:
            trips.append(trip)

    recorded = await record_fares(flight_legs, cycle_id)
    log.info("trips_built", count=len(trips), fares_recorded=recorded)
    return trips


def _build_flight_only_trip(leg: FlightLeg, price_index: PriceIndex) -> Optional[Trip]:
    route = _make_route_string(leg.origin, leg.destination)
    anomaly = price_index.check(leg)
    normal = anomaly.normal_price  # the route's usual price; None until there is history
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
        avg_historical_price_eur=normal,
        discount_pct=discount,
        departure_date=leg.departure_date,
        return_date=leg.return_date,
        nights=leg.stay_nights,
        data_confidence_score=leg.data_confidence_score,
        source_list=[leg.source],
        verdict=verdict,
    )
    trip.compute_totals()

    # Possible error fare: flagged by the source, or under 40% of the route's usual price
    far_below_usual = bool(normal and leg.price_eur < normal * 0.40)
    if leg.is_error_fare_hint or far_below_usual:
        trip.is_error_fare = True
        trip.deal_type = DealType.ERROR_FARE

    if anomaly.is_anomaly:
        _apply_anomaly_to_trip(trip, anomaly)
        trip.verdict = f"{trip.verdict} (Historical anomaly: {anomaly.description})"

    _finalize_trip(trip)
    return trip


def _build_package_trip(leg: FlightLeg) -> Optional[Trip]:
    """A flight + hotel package from a deal-site post: one price per person for both."""
    trip = Trip(
        trip_id=str(uuid.uuid4())[:8],
        deal_type=DealType.PACKAGE,
        route=_make_route_string(leg.origin, leg.destination),
        outbound_flight=leg,
        flight_cost_eur=leg.price_eur,
        departure_date=leg.departure_date,
        return_date=leg.return_date,
        nights=leg.stay_nights,
        data_confidence_score=leg.data_confidence_score,
        source_list=[leg.source],
        verdict="SEE THE OFFER",
    )
    trip.compute_totals()
    _finalize_trip(trip)
    return trip


def _build_hotel_only_trip(hotel: HotelDeal) -> Trip:
    """A hotel stay from a deal-site post; the price is per night as the post states it."""
    dated = hotel.check_in is not None
    trip = Trip(
        trip_id=str(uuid.uuid4())[:8],
        deal_type=DealType.HOTEL_ONLY,
        route=hotel.location,
        hotel=hotel,
        hotel_cost_eur=hotel.total_price_eur,
        departure_date=hotel.check_in,
        return_date=hotel.check_out,
        nights=hotel.nights if dated else None,
        data_confidence_score=hotel.data_confidence_score,
        source_list=[hotel.source],
        verdict="SEE THE OFFER",
    )
    trip.compute_totals()
    _finalize_trip(trip)
    return trip


def _build_complete_trip(leg: FlightLeg, hotel: HotelDeal, price_index: PriceIndex) -> Optional[Trip]:
    nights = hotel.nights
    route = _make_route_string(leg.origin, leg.destination)

    anomaly = price_index.check(leg)
    total_cost = calculate_trip_total(leg, None, hotel, [])
    normal = estimate_normal_price(route, anomaly.normal_price, None, nights)

    discount = compute_discount_pct(total_cost, normal)
    is_europe = _is_short_haul(leg.origin, leg.destination)
    confidence = compute_trip_confidence([leg.data_confidence_score, hotel.data_confidence_score])
    verdict = assign_verdict(total_cost, discount, confidence, is_europe)

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
        avg_historical_price_eur=anomaly.normal_price,
        discount_pct=discount,
        departure_date=leg.departure_date,
        return_date=leg.return_date,
        nights=nights,
        data_confidence_score=confidence,
        source_list=list({leg.source, hotel.source}),
        verdict=verdict,
    )

    # Historical anomaly on the flight leg
    if anomaly.is_anomaly:
        _apply_anomaly_to_trip(trip, anomaly)

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
        nights=onward_flight.stay_nights,
        data_confidence_score=confidence,
        source_list=list({repo_leg.source, onward_flight.source}),
        verdict=verdict,
    )
    _finalize_trip(trip)
    return trip
