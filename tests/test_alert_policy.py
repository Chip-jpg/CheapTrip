"""Tests for the alert policy (single source of truth for alert tiers)."""
from __future__ import annotations

from datetime import date

import pytest

from config import Settings
from filters.alert_policy import decide_tier
from filters.hard_filters import apply_hard_filters
from preferences import UserPreferences
from storage.models import AlertTier, BookingConfidence, DealType, FlightLeg, HotelDeal, Trip


def _trip(
    dest: str = "KRK",
    cost: float = 85.0,
    *,
    origin: str = "MXP",
    deal_type: DealType = DealType.FLIGHT_ONLY,
    booking_confidence: BookingConfidence = BookingConfidence.MEDIUM,
    discount: float | None = None,
    feasible: bool = True,
    error_fare: bool = False,
    anomaly: bool = False,
) -> Trip:
    leg = FlightLeg(
        origin=origin, destination=dest, price_eur=cost,
        departure_date=date(2026, 11, 6), source="test", data_confidence_score=0.8,
    )
    return Trip(
        deal_type=deal_type,
        route=f"{origin} → {dest}",
        outbound_flight=leg,
        departure_date=leg.departure_date,
        flight_cost_eur=cost,
        total_cost_eur=cost,
        discount_pct=discount,
        booking_confidence=booking_confidence,
        is_feasible=feasible,
        feasibility_notes=[] if feasible else ["WEEKEND trips: max 8h travel"],
        is_error_fare=error_fare,
        is_anomaly=anomaly,
        anomaly_description="Matches or beats all-time low of €300" if anomaly else None,
        data_confidence_score=0.8,
    )


SETTINGS = Settings(_env_file=None)
PREFS = UserPreferences()


def test_exceptional_short_haul_price_is_instant():
    decision = decide_tier(_trip("KRK", 22.0), SETTINGS, PREFS)
    assert decision.tier == AlertTier.INSTANT
    assert any("€25 short-haul backstop" in r for r in decision.reasons)


def test_ordinary_cheap_fare_without_history_is_digest():
    # The dry run had 663/706 deals instant because a flat €120 cap caught every Ryanair fare
    decision = decide_tier(_trip("KRK", 39.0), SETTINGS, PREFS)
    assert decision.tier == AlertTier.DIGEST


@pytest.mark.parametrize("cost,tier", [(240.0, AlertTier.INSTANT), (400.0, AlertTier.DIGEST)])
def test_long_haul_backstop(cost, tier):
    assert decide_tier(_trip("JFK", cost), SETTINGS, PREFS).tier == tier


def test_expensive_trip_is_digest():
    decision = decide_tier(_trip("KRK", 250.0), SETTINGS, PREFS)
    assert decision.tier == AlertTier.DIGEST
    assert decision.reasons == ["no instant criteria met"]


def test_priority_destination_is_instant_regardless_of_price():
    prefs = UserPreferences(priority_destinations=["NRT"])
    decision = decide_tier(_trip("NRT", 900.0), SETTINGS, prefs)
    assert decision.tier == AlertTier.INSTANT
    assert "priority destination NRT" in decision.reasons


def test_historical_anomaly_is_instant():
    decision = decide_tier(_trip("JFK", 600.0, anomaly=True), SETTINGS, PREFS)
    assert decision.tier == AlertTier.INSTANT
    assert any(r.startswith("historical anomaly") for r in decision.reasons)


def test_error_fare_is_instant():
    decision = decide_tier(_trip("JFK", 600.0, error_fare=True), SETTINGS, PREFS)
    assert decision.tier == AlertTier.INSTANT


def test_flight_discount_threshold():
    decision = decide_tier(_trip("KRK", 200.0, discount=65.0), SETTINGS, PREFS)
    assert decision.tier == AlertTier.INSTANT


def test_low_booking_confidence_is_held_back():
    decision = decide_tier(_trip("KRK", 20.0, booking_confidence=BookingConfidence.LOW), SETTINGS, PREFS)
    assert decision.tier == AlertTier.DIGEST
    assert any("booking confidence LOW below MEDIUM" in r for r in decision.reasons)


def test_confidence_minimum_is_configurable():
    settings = Settings(_env_file=None, booking_confidence_min_for_instant="HIGH")
    decision = decide_tier(_trip("KRK", 20.0, booking_confidence=BookingConfidence.MEDIUM), settings, PREFS)
    assert decision.tier == AlertTier.DIGEST
    low = Settings(_env_file=None, booking_confidence_min_for_instant="low")
    assert decide_tier(_trip("KRK", 20.0, booking_confidence=BookingConfidence.LOW), low, PREFS).tier == AlertTier.INSTANT


def test_infeasible_priority_trip_is_digest():
    prefs = UserPreferences(priority_destinations=["SYD"])
    decision = decide_tier(_trip("SYD", 900.0, feasible=False), SETTINGS, prefs)
    assert decision.tier == AlertTier.DIGEST
    assert any("held back" in r for r in decision.reasons)


def test_secondary_london_airport_uses_short_haul_threshold():
    # STN used to be missing from the Europe set, so it got the long-haul threshold.
    decision = decide_tier(_trip("STN", 200.0), SETTINGS, PREFS)
    assert decision.tier == AlertTier.DIGEST


@pytest.mark.parametrize("discount,kept", [(69.0, False), (75.0, True)])
def test_low_rated_hotel_needs_configured_discount(discount, kept, monkeypatch):
    trip = _trip("KRK", 150.0, deal_type=DealType.COMPLETE_TRIP, discount=discount)
    trip.hotel = HotelDeal(
        name="Budget Inn", location="Krakow", price_per_night_eur=20.0, nights=3,
        total_price_eur=60.0, rating=5.0, meets_quality_threshold=False,
    )
    monkeypatch.setattr(
        "filters.hard_filters.get_preferences",
        lambda: UserPreferences(hotel_low_rating_discount_threshold=72.0),
    )
    instant, digest = apply_hard_filters([trip])
    assert bool(instant or digest) is kept


def test_hard_filters_store_reasons_on_trip():
    trip = _trip("KRK", 20.0)
    instant, _ = apply_hard_filters([trip])
    assert instant and instant[0].alert_reasons
