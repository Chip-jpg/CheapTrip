"""Tests for deal categorization rules."""
from __future__ import annotations

from datetime import date

from storage.models import (
    DealCategory,
    DealType,
    FlightLeg,
    HotelDeal,
    Trip,
    TripLengthProfile,
)
from trip_builder.categorizer import categorize_trip


def _flight_leg(dest: str = "KRK") -> FlightLeg:
    return FlightLeg(
        origin="MXP",
        destination=dest,
        price_eur=80.0,
        departure_date=date(2026, 7, 1),
        return_date=date(2026, 7, 4),
        source="test",
    )


def _make_flight_trip(dest: str = "KRK", discount: float = 0.0, is_error: bool = False) -> Trip:
    t = Trip(
        trip_id="t1",
        deal_type=DealType.FLIGHT_ONLY,
        route=f"MXP → {dest}",
        outbound_flight=_flight_leg(dest),
        flight_cost_eur=80.0,
        total_cost_eur=80.0,
        discount_pct=discount,
        departure_date=date(2026, 7, 1),
        return_date=date(2026, 7, 4),
        nights=3,
        data_confidence_score=0.8,
        source_list=["test"],
        is_error_fare=is_error,
    )
    if is_error:
        t.deal_type = DealType.ERROR_FARE
    return t


def _make_complete_trip(dest: str = "KRK", discount: float = 0.0, rating: float = 8.5, nights: int = 3) -> Trip:
    hotel = HotelDeal(
        name="Test Hotel",
        location=dest,
        price_per_night_eur=50.0,
        nights=nights,
        total_price_eur=50.0 * nights,
        rating=rating,
        source="test",
    )
    return Trip(
        trip_id="t2",
        deal_type=DealType.COMPLETE_TRIP,
        route=f"MXP → {dest}",
        outbound_flight=_flight_leg(dest),
        hotel=hotel,
        flight_cost_eur=80.0,
        hotel_cost_eur=hotel.total_price_eur,
        total_cost_eur=80.0 + hotel.total_price_eur,
        discount_pct=discount,
        departure_date=date(2026, 7, 1),
        return_date=date(2026, 7, 4),
        nights=nights,
        data_confidence_score=0.8,
        source_list=["test"],
    )


class TestCategorizeTrip:
    def test_error_fare_highest_priority(self):
        trip = _make_flight_trip(is_error=True, discount=75.0)
        assert categorize_trip(trip) == DealCategory.ERROR_FARE

    def test_flight_steal_high_discount(self):
        trip = _make_flight_trip(discount=65.0)
        assert categorize_trip(trip) == DealCategory.FLIGHT_STEAL

    def test_hotel_steal_high_discount_hotel_only(self):
        hotel = HotelDeal(
            name="Test Hotel",
            location="KRK",
            price_per_night_eur=50.0,
            nights=3,
            total_price_eur=150.0,
            rating=7.5,
            source="test",
        )
        trip = Trip(
            trip_id="t_hs",
            deal_type=DealType.HOTEL_ONLY,
            route="Hotel Deal",
            hotel=hotel,
            hotel_cost_eur=150.0,
            total_cost_eur=150.0,
            discount_pct=65.0,
            data_confidence_score=0.8,
            source_list=["test"],
        )
        assert categorize_trip(trip) == DealCategory.HOTEL_STEAL

    def test_complete_trip_high_discount_luxury(self):
        trip = _make_complete_trip(discount=65.0, rating=8.5)
        # COMPLETE_TRIP with high rating + discount → LUXURY_DISCOUNT (rule fires before LONG_HAUL check)
        assert categorize_trip(trip) == DealCategory.LUXURY_DISCOUNT

    def test_long_haul_destination(self):
        # Long-haul requires nights >= 5 to distinguish from a quick layover
        trip = _make_flight_trip(dest="NRT")
        trip.nights = 7
        assert categorize_trip(trip) == DealCategory.LONG_HAUL_ADVENTURE

    def test_beach_destination(self):
        trip = _make_flight_trip(dest="PMI")
        assert categorize_trip(trip) == DealCategory.BEACH_HOLIDAY

    def test_weekend_escape_europe_short(self):
        trip = _make_complete_trip(dest="KRK", nights=2)
        trip.trip_length_profile = TripLengthProfile.WEEKEND
        result = categorize_trip(trip)
        assert result == DealCategory.WEEKEND_ESCAPE

    def test_luxury_discount_high_rating_good_discount(self):
        trip = _make_complete_trip(dest="VIE", discount=42.0, rating=9.2)
        result = categorize_trip(trip)
        assert result == DealCategory.LUXURY_DISCOUNT

    def test_default_is_budget_city_break(self):
        trip = _make_flight_trip(dest="WAW", discount=10.0)
        result = categorize_trip(trip)
        assert result == DealCategory.BUDGET_CITY_BREAK

    def test_no_outbound_flight_does_not_crash(self):
        trip = Trip(
            trip_id="t3",
            deal_type=DealType.FLIGHT_ONLY,
            route="unknown",
            total_cost_eur=100.0,
            data_confidence_score=0.5,
            source_list=["test"],
        )
        result = categorize_trip(trip)
        assert isinstance(result, DealCategory)
