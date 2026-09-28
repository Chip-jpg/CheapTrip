"""Tests for filtering, deduplication, and time decay."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

from filters.hard_filters import apply_hard_filters
from filters.time_decay import apply_time_decay
from storage.models import AlertTier, BookingConfidence, DealType, FlightLeg, Trip


def _make_flight_trip(
    origin: str,
    dest: str,
    cost: float,
    confidence: float = 0.8,
    scraped_age_hours: float = 0.0,
    booking_confidence: BookingConfidence = BookingConfidence.MEDIUM,
) -> Trip:
    scraped_at = datetime.utcnow() - timedelta(hours=scraped_age_hours)
    leg = FlightLeg(
        origin=origin,
        destination=dest,
        price_eur=cost,
        departure_date=date(2025, 8, 1),
        source="test",
        data_confidence_score=confidence,
        scraped_at=scraped_at,
    )
    trip = Trip(
        deal_type=DealType.FLIGHT_ONLY,
        route=f"{origin} → {dest}",
        outbound_flight=leg,
        flight_cost_eur=cost,
        total_cost_eur=cost,
        departure_date=date(2025, 8, 1),
        data_confidence_score=confidence,
        source_list=["test"],
        booking_confidence=booking_confidence,
    )
    trip.hash = trip.compute_hash()
    return trip


class TestHardFilters:
    def test_cheap_europe_trip_is_instant(self):
        trip = _make_flight_trip("MXP", "KRK", 85.0)
        instant, digest = apply_hard_filters([trip])
        assert len(instant) == 1
        assert instant[0].alert_tier == AlertTier.INSTANT

    def test_expensive_europe_trip_goes_to_digest(self):
        trip = _make_flight_trip("MXP", "KRK", 250.0)
        instant, digest = apply_hard_filters([trip])
        assert len(instant) == 0
        assert len(digest) == 1

    def test_very_expensive_trip_goes_to_digest(self):
        trip = _make_flight_trip("MXP", "KRK", 999.0)
        instant, digest = apply_hard_filters([trip])
        assert len(instant) == 0
        assert len(digest) == 1

    def test_cheap_longhaul_is_instant(self):
        trip = _make_flight_trip("MXP", "JFK", 320.0)
        instant, digest = apply_hard_filters([trip])
        assert len(instant) == 1

    def test_zero_cost_discarded(self):
        trip = _make_flight_trip("MXP", "KRK", 0.0)
        instant, digest = apply_hard_filters([trip])
        assert len(instant) == 0
        assert len(digest) == 0


class TestBudgetFilter:
    def test_trip_above_max_budget_is_discarded(self):
        trip = _make_flight_trip("MXP", "KRK", 85.0)
        with patch("filters.hard_filters.get_preferences") as mock_prefs:
            mock_prefs.return_value.max_trip_budget = 50.0
            mock_prefs.return_value.excluded_destinations = []
            instant, digest = apply_hard_filters([trip])
        assert len(instant) == 0
        assert len(digest) == 0

    def test_trip_below_max_budget_passes(self):
        trip = _make_flight_trip("MXP", "KRK", 85.0)
        with patch("filters.hard_filters.get_preferences") as mock_prefs:
            mock_prefs.return_value.max_trip_budget = 200.0
            mock_prefs.return_value.excluded_destinations = []
            instant, digest = apply_hard_filters([trip])
        assert len(instant) == 1

    def test_no_budget_cap_allows_all(self):
        trip = _make_flight_trip("MXP", "KRK", 85.0)
        with patch("filters.hard_filters.get_preferences") as mock_prefs:
            mock_prefs.return_value.max_trip_budget = None
            mock_prefs.return_value.excluded_destinations = []
            instant, digest = apply_hard_filters([trip])
        assert len(instant) == 1


class TestTimeDecay:
    def test_fresh_instant_stays_instant(self):
        trip = _make_flight_trip("MXP", "KRK", 85.0, scraped_age_hours=1.0)
        trip.alert_tier = AlertTier.INSTANT
        instant, digest, archive = apply_time_decay([trip])
        assert len(instant) == 1

    def test_stale_instant_moves_to_digest(self):
        trip = _make_flight_trip("MXP", "KRK", 85.0, scraped_age_hours=10.0)
        trip.alert_tier = AlertTier.INSTANT
        instant, digest, archive = apply_time_decay([trip])
        assert len(instant) == 0
        assert len(digest) == 1

    def test_old_deal_goes_to_archive(self):
        trip = _make_flight_trip("MXP", "KRK", 85.0, scraped_age_hours=30.0)
        trip.alert_tier = AlertTier.INSTANT
        instant, digest, archive = apply_time_decay([trip])
        assert len(archive) == 1

    def test_digest_tier_unaffected_within_24h(self):
        trip = _make_flight_trip("MXP", "KRK", 200.0, scraped_age_hours=8.0)
        trip.alert_tier = AlertTier.DIGEST
        instant, digest, archive = apply_time_decay([trip])
        assert len(digest) == 1

    def test_fresh_digest_promoted_to_instant(self):
        trip = _make_flight_trip("MXP", "KRK", 85.0, scraped_age_hours=1.0)
        trip.alert_tier = AlertTier.DIGEST
        instant, digest, archive = apply_time_decay([trip])
        assert len(instant) == 1
        assert instant[0].alert_tier == AlertTier.INSTANT
