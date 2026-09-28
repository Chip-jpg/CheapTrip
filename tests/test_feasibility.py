"""Tests for trip feasibility engine."""
from __future__ import annotations

from datetime import date

from storage.models import DealType, FlightLeg, Trip, TripLengthProfile
from trip_builder.feasibility import check_feasibility, estimate_travel_hours, get_trip_profile


def _make_trip(origin: str = "MXP", dest: str = "KRK", nights: int = 3) -> Trip:
    leg = FlightLeg(
        origin=origin,
        destination=dest,
        price_eur=100.0,
        departure_date=date(2026, 7, 1),
        return_date=date(2026, 7, 4),
        source="test",
    )
    return Trip(
        trip_id="t1",
        deal_type=DealType.FLIGHT_ONLY,
        route=f"{origin} → {dest}",
        outbound_flight=leg,
        flight_cost_eur=100.0,
        total_cost_eur=100.0,
        departure_date=date(2026, 7, 1),
        return_date=date(2026, 7, 4),
        nights=nights,
        data_confidence_score=0.8,
        source_list=["test"],
    )


class TestEstimateTravelHours:
    def test_known_pair_direct(self):
        hours = estimate_travel_hours("MXP", "NRT")
        assert hours > 10.0  # long-haul

    def test_same_airport(self):
        hours = estimate_travel_hours("MXP", "MXP")
        assert hours == 0.0

    def test_unknown_pair_returns_conservative(self):
        hours = estimate_travel_hours("XYZ", "ABC")
        assert hours > 0.0

    def test_short_haul_europe(self):
        hours = estimate_travel_hours("MXP", "KRK")
        assert hours < 5.0


class TestGetTripProfile:
    def test_2_nights_is_weekend(self):
        assert get_trip_profile(2) == TripLengthProfile.WEEKEND

    def test_3_nights_is_weekend(self):
        assert get_trip_profile(3) == TripLengthProfile.WEEKEND

    def test_5_nights_is_short(self):
        assert get_trip_profile(5) == TripLengthProfile.SHORT

    def test_10_nights_is_medium(self):
        assert get_trip_profile(10) == TripLengthProfile.MEDIUM

    def test_20_nights_is_long(self):
        assert get_trip_profile(20) == TripLengthProfile.LONG

    def test_none_returns_short(self):
        assert get_trip_profile(None) == TripLengthProfile.SHORT


class TestCheckFeasibility:
    def test_short_european_trip_is_feasible(self):
        trip = _make_trip("MXP", "KRK", nights=3)
        feasible, notes = check_feasibility(trip, TripLengthProfile.WEEKEND)
        assert feasible is True
        assert notes == []

    def test_longhaul_on_weekend_is_infeasible(self):
        trip = _make_trip("MXP", "NRT", nights=2)
        feasible, notes = check_feasibility(trip, TripLengthProfile.WEEKEND)
        assert feasible is False
        assert len(notes) > 0

    def test_long_trip_minimum_nights(self):
        # LONG profile requires min 5 nights; 2 nights should fail
        trip = _make_trip("MXP", "KRK", nights=2)
        feasible, notes = check_feasibility(trip, TripLengthProfile.LONG)
        assert feasible is False
        assert any("night" in n.lower() for n in notes)

    def test_long_trip_with_enough_nights(self):
        trip = _make_trip("MXP", "KRK", nights=10)
        feasible, notes = check_feasibility(trip, TripLengthProfile.LONG)
        assert feasible is True

    def test_feasibility_no_flight_leg(self):
        trip = Trip(
            trip_id="t2",
            deal_type=DealType.FLIGHT_ONLY,
            route="unknown",
            total_cost_eur=100.0,
            data_confidence_score=0.5,
            source_list=["test"],
        )
        feasible, notes = check_feasibility(trip, TripLengthProfile.SHORT)
        # Should not crash with no outbound_flight
        assert isinstance(feasible, bool)
