"""Tests for confidence score calculations."""
from __future__ import annotations

from datetime import date, timedelta

from storage.models import RawFlightResult, RawHotelResult
from utils.confidence import compute_flight_confidence, compute_hotel_confidence
from utils.timeutil import utcnow


def _raw_flight(
    source: str = "ryanair",
    price: float = 100.0,
    age_hours: float = 0.5,
    airline: str = "Test Air",
    booking_url: str = "https://example.com",
) -> RawFlightResult:
    return RawFlightResult(
        origin="MXP",
        destination="KRK",
        price=price,
        currency="EUR",
        departure_date=date(2025, 8, 1),
        return_date=date(2025, 8, 5),
        airline=airline,
        booking_url=booking_url,
        source=source,
        scraped_at=utcnow() - timedelta(hours=age_hours),
    )


class TestFlightConfidence:
    def test_single_fresh_high_reliability_source(self):
        results = [_raw_flight("ryanair", age_hours=0.5)]
        score = compute_flight_confidence(results)
        assert 0.7 <= score <= 1.0

    def test_multiple_sources_boosts_score(self):
        single = compute_flight_confidence([_raw_flight("ryanair")])
        multi = compute_flight_confidence([
            _raw_flight("ryanair"),
            _raw_flight("google_flights"),
            _raw_flight("kayak"),
        ])
        assert multi > single

    def test_stale_data_lowers_score(self):
        fresh = compute_flight_confidence([_raw_flight(age_hours=0.5)])
        stale = compute_flight_confidence([_raw_flight(age_hours=48.0)])
        assert fresh > stale

    def test_empty_returns_zero(self):
        assert compute_flight_confidence([]) == 0.0

    def test_score_bounded_0_to_1(self):
        score = compute_flight_confidence([_raw_flight()])
        assert 0.0 <= score <= 1.0


class TestHotelConfidence:
    def test_basic_hotel_confidence(self):
        result = RawHotelResult(
            name="Grand Hotel",
            location="Krakow",
            price_per_night=30.0,
            currency="EUR",
            nights=3,
            rating=8.5,
            booking_url="https://example.com",
            source="booking_com_api",
        )
        score = compute_hotel_confidence([result])
        assert 0.5 <= score <= 1.0

    def test_empty_returns_zero(self):
        assert compute_hotel_confidence([]) == 0.0
