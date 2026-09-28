"""Tests for data models and core computation."""
from __future__ import annotations

from datetime import date

from storage.models import (
    DealType,
    FlightLeg,
    HotelDeal,
    RepositioningLeg,
    Trip,
)
from trip_builder.cost_calculator import (
    assign_verdict,
    calculate_trip_total,
    compute_discount_pct,
)


class TestFlightLeg:
    def test_airport_codes_uppercased(self):
        leg = FlightLeg(
            origin="mxp",
            destination="krk",
            price_eur=28.0,
            departure_date=date(2025, 6, 15),
            source="test",
            data_confidence_score=0.8,
        )
        assert leg.origin == "MXP"
        assert leg.destination == "KRK"


class TestHotelDeal:
    def test_total_computed_from_nights(self):
        h = HotelDeal(
            name="Test Hotel",
            location="Krakow",
            price_per_night_eur=30.0,
            nights=3,
            total_price_eur=999.0,  # Wrong — should be auto-corrected
            source="test",
            data_confidence_score=0.7,
        )
        assert h.total_price_eur == 90.0  # 30 × 3

    def test_exact_total_accepted(self):
        h = HotelDeal(
            name="Test Hotel",
            location="Krakow",
            price_per_night_eur=30.0,
            nights=3,
            total_price_eur=90.0,
            source="test",
            data_confidence_score=0.7,
        )
        assert h.total_price_eur == 90.0


class TestTripHash:
    def test_hash_is_deterministic(self):
        leg = FlightLeg(
            origin="MXP",
            destination="KRK",
            price_eur=28.0,
            departure_date=date(2025, 6, 15),
            source="test",
            data_confidence_score=0.8,
        )
        trip = Trip(
            deal_type=DealType.FLIGHT_ONLY,
            route="Milan → Krakow",
            outbound_flight=leg,
            total_cost_eur=28.0,
            departure_date=date(2025, 6, 15),
            data_confidence_score=0.8,
        )
        h1 = trip.compute_hash()
        h2 = trip.compute_hash()
        assert h1 == h2

    def test_different_prices_produce_different_hashes(self):
        def make_trip(price: float) -> Trip:
            leg = FlightLeg(
                origin="MXP",
                destination="KRK",
                price_eur=price,
                departure_date=date(2025, 6, 15),
                source="test",
                data_confidence_score=0.8,
            )
            trip = Trip(
                deal_type=DealType.FLIGHT_ONLY,
                route="Milan → Krakow",
                outbound_flight=leg,
                total_cost_eur=price,
                departure_date=date(2025, 6, 15),
                data_confidence_score=0.8,
            )
            return trip

        assert make_trip(28.0).compute_hash() != make_trip(50.0).compute_hash()


class TestCostCalculator:
    def test_total_cost_flight_only(self):
        leg = FlightLeg(
            origin="MXP", destination="KRK", price_eur=28.0,
            departure_date=date(2025, 6, 15), source="test",
            data_confidence_score=0.8,
        )
        total = calculate_trip_total(leg, None, None, [])
        assert total == 28.0

    def test_total_cost_with_hotel(self):
        leg = FlightLeg(
            origin="MXP", destination="KRK", price_eur=28.0,
            departure_date=date(2025, 6, 15), source="test",
            data_confidence_score=0.8,
        )
        # price_per_night=29.67 × 3 nights = 89.01 (model auto-corrects total)
        hotel = HotelDeal(
            name="Test", location="Krakow", price_per_night_eur=29.67,
            nights=3, total_price_eur=89.01, source="test",
            data_confidence_score=0.7,
        )
        total = calculate_trip_total(leg, None, hotel, [])
        assert total == 117.01

    def test_total_cost_with_repositioning(self):
        leg = FlightLeg(
            origin="LHR", destination="JFK", price_eur=220.0,
            departure_date=date(2025, 6, 15), source="test",
            data_confidence_score=0.8,
        )
        repo = RepositioningLeg(
            origin="MXP", hub="LHR", transport_type="flight",
            price_eur=60.0, duration_hours=2.5, source="test",
            data_confidence_score=0.7,
        )
        total = calculate_trip_total(leg, None, None, [repo], fees=0.0)
        # onward flight + repositioning leg
        assert total == 280.0

    def test_discount_pct_calculated(self):
        pct = compute_discount_pct(117.0, 300.0)
        assert pct == 61.0

    def test_no_discount_when_normal_not_available(self):
        assert compute_discount_pct(100.0, None) is None

    def test_no_discount_when_actual_higher(self):
        assert compute_discount_pct(350.0, 300.0) is None


class TestAssignVerdict:
    def test_book_now_europe_cheap(self):
        assert assign_verdict(75.0, 70.0, 0.9, True) == "BOOK NOW"

    def test_book_now_europe_boundary(self):
        assert assign_verdict(115.0, 60.0, 0.8, True) == "BOOK NOW"

    def test_verify_book_low_confidence(self):
        assert assign_verdict(115.0, 60.0, 0.5, True) == "VERIFY & BOOK"

    def test_book_now_longhaul(self):
        assert assign_verdict(280.0, 65.0, 0.9, False) == "BOOK NOW"

    def test_monitor_expensive(self):
        assert assign_verdict(800.0, 20.0, 0.7, False) == "MONITOR"


class TestTripComputeTotals:
    def test_compute_totals_sets_total_and_discount(self):
        trip = Trip(
            deal_type=DealType.COMPLETE_TRIP,
            route="Milan → Krakow",
            flight_cost_eur=28.0,
            hotel_cost_eur=89.0,
            normal_price_eur=300.0,
            departure_date=date(2025, 6, 15),
            data_confidence_score=0.8,
        )
        trip.compute_totals()
        assert trip.total_cost_eur == 117.0
        assert trip.discount_pct == 61.0
