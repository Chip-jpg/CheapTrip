"""
Deal Categorization Engine

Assigns a DealCategory to every Trip using pure deterministic rules.
No AI involved. Categories drive Telegram display labels.
"""
from __future__ import annotations

from preferences import get_preferences
from storage.models import DealCategory, DealType, Trip, TripLengthProfile
from utils import airports


def _dest(trip: Trip) -> str:
    if trip.outbound_flight:
        return trip.outbound_flight.destination
    return ""


def categorize_trip(trip: Trip) -> DealCategory:
    """
    Assign the most appropriate DealCategory using priority-ordered rules.
    """
    dest = _dest(trip)
    nights = trip.nights or 0
    discount = trip.discount_pct or 0.0
    has_hotel = trip.hotel is not None
    hotel_rating = (trip.hotel.rating or 0.0) if has_hotel else 0.0
    prefs = get_preferences()

    # 1. Error fare — highest priority
    if trip.is_error_fare:
        return DealCategory.ERROR_FARE

    # 2. Hotel-only steal
    if trip.deal_type == DealType.HOTEL_ONLY and discount >= 40.0:
        return DealCategory.HOTEL_STEAL

    # 3. Flight-only steal
    if trip.deal_type == DealType.FLIGHT_ONLY and discount >= 40.0:
        return DealCategory.FLIGHT_STEAL

    # 4. Repositioned trip — package arbitrage
    if trip.deal_type == DealType.REPOSITIONED:
        return DealCategory.PACKAGE_ARBITRAGE

    # 5. Luxury discount: high-rated hotel with meaningful discount
    if has_hotel and hotel_rating >= prefs.preferred_hotel_rating and discount >= 30.0:
        return DealCategory.LUXURY_DISCOUNT

    origin = trip.outbound_flight.origin if trip.outbound_flight else None
    long_haul = bool(dest) and airports.is_long_haul(dest, origin)

    # 6. Long-haul adventure
    if long_haul and nights >= 5:
        return DealCategory.LONG_HAUL_ADVENTURE

    # 7. Beach holiday
    if airports.is_beach(dest):
        return DealCategory.BEACH_HOLIDAY

    # 8. Weekend escape: short Europe trip
    profile = trip.trip_length_profile
    is_weekend = (
        profile == TripLengthProfile.WEEKEND
        or (nights >= 2 and nights <= 4)
    )
    is_europe = bool(dest) and not long_haul

    if is_weekend and is_europe and has_hotel:
        return DealCategory.WEEKEND_ESCAPE

    # 9. Budget city break (default for Europe)
    if is_europe:
        return DealCategory.BUDGET_CITY_BREAK

    # Fallback
    return DealCategory.BUDGET_CITY_BREAK
