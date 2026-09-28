"""Tests for message formatting."""
from __future__ import annotations

import re
from datetime import date

from ai_layer.formatter import format_complete_trip, format_digest, format_flight_only
from storage.models import DealType, FlightLeg, HotelDeal, Trip


def _make_complete_trip() -> Trip:
    leg = FlightLeg(
        origin="MXP",
        destination="KRK",
        price_eur=28.0,
        departure_date=date(2025, 8, 1),
        return_date=date(2025, 8, 4),
        airline="Ryanair",
        booking_url="https://ryanair.com",
        source="skyscanner_api",
        data_confidence_score=0.87,
    )
    hotel = HotelDeal(
        name="Grand Hostel Krakow",
        location="Krakow",
        price_per_night_eur=29.67,
        nights=3,
        total_price_eur=89.0,
        rating=8.5,
        booking_url="https://booking.com",
        source="booking_com_api",
        data_confidence_score=0.82,
    )
    trip = Trip(
        deal_type=DealType.COMPLETE_TRIP,
        route="Milan → Krakow",
        outbound_flight=leg,
        hotel=hotel,
        flight_cost_eur=28.0,
        hotel_cost_eur=89.0,
        total_cost_eur=117.0,
        normal_price_eur=300.0,
        discount_pct=61.0,
        departure_date=date(2025, 8, 1),
        return_date=date(2025, 8, 4),
        nights=3,
        data_confidence_score=0.87,
        source_list=["skyscanner_api", "booking_com_api"],
        verdict="BOOK NOW",
    )
    trip.hash = trip.compute_hash()
    return trip


class TestFormatCompleteTrip:
    def test_contains_route(self):
        msg = format_complete_trip(_make_complete_trip())
        assert "Milan → Krakow" in msg

    def test_contains_total_cost(self):
        msg = format_complete_trip(_make_complete_trip())
        assert "€117" in msg

    def test_contains_flight_cost(self):
        msg = format_complete_trip(_make_complete_trip())
        assert "€28" in msg

    def test_contains_hotel_cost(self):
        msg = format_complete_trip(_make_complete_trip())
        assert "€89" in msg

    def test_contains_confidence(self):
        msg = format_complete_trip(_make_complete_trip())
        assert "0.87" in msg

    def test_contains_verdict(self):
        msg = format_complete_trip(_make_complete_trip())
        assert "BOOK NOW" in msg

    def test_no_invented_prices(self):
        """Prices in the message must match those in the Trip object."""
        trip = _make_complete_trip()
        msg = format_complete_trip(trip)
        prices_in_msg = {int(p) for p in re.findall(r"€(\d+)", msg)}
        expected = {28, 89, 117, 300}
        # Every price in the message should be from the known set
        # 29.67/night rounds to 30 at :.0f format — both are valid representations
        assert prices_in_msg.issubset(expected | {29, 30})  # per-night rounding


class TestFormatFlightOnly:
    def test_contains_emoji(self):
        leg = FlightLeg(
            origin="MXP", destination="JFK", price_eur=249.0,
            departure_date=date(2025, 8, 1), source="test",
            data_confidence_score=0.8,
        )
        trip = Trip(
            deal_type=DealType.FLIGHT_ONLY,
            route="Milan → New York",
            outbound_flight=leg,
            total_cost_eur=249.0,
            normal_price_eur=700.0,
            data_confidence_score=0.8,
            verdict="BOOK NOW",
        )
        msg = format_flight_only(trip)
        assert "✈️" in msg
        assert "€249" in msg


class TestFormatDigest:
    def test_digest_header_present(self):
        trips = [_make_complete_trip()]
        msg = format_digest(trips)
        assert "DIGEST" in msg.upper()

    def test_digest_count_shown(self):
        trips = [_make_complete_trip(), _make_complete_trip()]
        msg = format_digest(trips)
        assert "2" in msg
