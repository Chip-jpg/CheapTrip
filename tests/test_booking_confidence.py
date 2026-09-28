"""Tests for the booking confidence model."""
from __future__ import annotations

from storage.models import BookingConfidence
from utils.confidence import compute_booking_confidence


class TestComputeBookingConfidence:
    def test_high_confidence_all_criteria_met(self):
        result = compute_booking_confidence(
            data_confidence_score=0.85,
            source_count=3,
            has_booking_url=True,
            is_error_fare=False,
        )
        assert result == BookingConfidence.HIGH

    def test_medium_with_url_decent_score(self):
        result = compute_booking_confidence(
            data_confidence_score=0.60,
            source_count=1,
            has_booking_url=True,
            is_error_fare=False,
        )
        assert result == BookingConfidence.MEDIUM

    def test_medium_multiple_sources_no_url(self):
        result = compute_booking_confidence(
            data_confidence_score=0.70,
            source_count=2,
            has_booking_url=False,
            is_error_fare=False,
        )
        assert result == BookingConfidence.MEDIUM

    def test_low_single_source_no_url(self):
        result = compute_booking_confidence(
            data_confidence_score=0.40,
            source_count=1,
            has_booking_url=False,
            is_error_fare=False,
        )
        assert result == BookingConfidence.LOW

    def test_error_fare_with_url_is_medium(self):
        result = compute_booking_confidence(
            data_confidence_score=0.90,
            source_count=5,
            has_booking_url=True,
            is_error_fare=True,
        )
        assert result == BookingConfidence.MEDIUM

    def test_error_fare_without_url_is_low(self):
        result = compute_booking_confidence(
            data_confidence_score=0.90,
            source_count=5,
            has_booking_url=False,
            is_error_fare=True,
        )
        assert result == BookingConfidence.LOW

    def test_borderline_high_score_single_source(self):
        # score >= 0.75 but source_count=1 → not HIGH
        result = compute_booking_confidence(
            data_confidence_score=0.80,
            source_count=1,
            has_booking_url=True,
            is_error_fare=False,
        )
        assert result == BookingConfidence.MEDIUM

    def test_zero_score_is_low(self):
        result = compute_booking_confidence(
            data_confidence_score=0.0,
            source_count=0,
            has_booking_url=False,
        )
        assert result == BookingConfidence.LOW

    def test_returns_booking_confidence_enum(self):
        result = compute_booking_confidence(0.5, 1, True)
        assert isinstance(result, BookingConfidence)
