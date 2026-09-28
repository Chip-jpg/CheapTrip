from __future__ import annotations

from datetime import datetime
from typing import List

from storage.models import BookingConfidence, RawFlightResult, RawHotelResult

# Source reliability weights (0.0–1.0)
SOURCE_RELIABILITY: dict[str, float] = {
    "ryanair": 0.95,          # airline's own fare API
    "skyscanner_api": 0.95,
    "google_flights": 0.90,
    "booking_com_api": 0.90,
    "kayak": 0.85,
    "momondo": 0.85,
    "secret_flying": 0.80,
    "holiday_pirates": 0.80,
    "going": 0.80,
    "travelzoo": 0.78,
    "agoda": 0.82,
    "hotels_com": 0.82,
    "cache": 0.30,
    "unknown": 0.50,
}


def _freshness_score(scraped_at: datetime) -> float:
    hours_ago = (datetime.utcnow() - scraped_at).total_seconds() / 3600
    if hours_ago < 1:
        return 1.0
    if hours_ago < 6:
        return 0.85
    if hours_ago < 24:
        return 0.65
    return 0.30


def _source_count_score(n: int) -> float:
    if n >= 3:
        return 1.0
    if n == 2:
        return 0.75
    return 0.50


def _field_completeness_flight(r: RawFlightResult) -> float:
    total, present = 7, 0
    if r.origin:
        present += 1
    if r.destination:
        present += 1
    if r.price > 0:
        present += 1
    if r.departure_date:
        present += 1
    if r.return_date:
        present += 1
    if r.airline:
        present += 1
    if r.booking_url:
        present += 1
    return present / total


def _field_completeness_hotel(r: RawHotelResult) -> float:
    total, present = 6, 0
    if r.name:
        present += 1
    if r.location:
        present += 1
    if r.price_per_night > 0:
        present += 1
    if r.nights > 0:
        present += 1
    if r.rating:
        present += 1
    if r.booking_url:
        present += 1
    return present / total


def compute_flight_confidence(results: List[RawFlightResult]) -> float:
    if not results:
        return 0.0
    freshness = sum(_freshness_score(r.scraped_at) for r in results) / len(results)
    source_count = _source_count_score(len(set(r.source for r in results)))
    completeness = sum(_field_completeness_flight(r) for r in results) / len(results)
    reliability = sum(
        SOURCE_RELIABILITY.get(r.source, 0.50) for r in results
    ) / len(results)
    score = (
        freshness * 0.30
        + source_count * 0.20
        + completeness * 0.25
        + reliability * 0.25
    )
    return round(min(max(score, 0.0), 1.0), 3)


def compute_hotel_confidence(results: List[RawHotelResult]) -> float:
    if not results:
        return 0.0
    freshness = sum(_freshness_score(r.scraped_at) for r in results) / len(results)
    source_count = _source_count_score(len(set(r.source for r in results)))
    completeness = sum(_field_completeness_hotel(r) for r in results) / len(results)
    reliability = sum(
        SOURCE_RELIABILITY.get(r.source, 0.50) for r in results
    ) / len(results)
    score = (
        freshness * 0.30
        + source_count * 0.20
        + completeness * 0.25
        + reliability * 0.25
    )
    return round(min(max(score, 0.0), 1.0), 3)


def compute_trip_confidence(component_scores: List[float]) -> float:
    if not component_scores:
        return 0.0
    return round(sum(component_scores) / len(component_scores), 3)


def compute_booking_confidence(
    data_confidence_score: float,
    source_count: int,
    has_booking_url: bool,
    is_error_fare: bool = False,
) -> BookingConfidence:
    """
    Assign a human-readable booking confidence tier.

    HIGH  = multiple verified sources, direct booking URL available
    MEDIUM = partially verified or single source with URL
    LOW   = unverified, no URL, or error fare with uncertainty
    """
    # Error fares carry uncertainty even with good data
    if is_error_fare:
        return BookingConfidence.MEDIUM if has_booking_url else BookingConfidence.LOW

    if data_confidence_score >= 0.75 and source_count >= 2 and has_booking_url:
        return BookingConfidence.HIGH
    if data_confidence_score >= 0.50 and has_booking_url:
        return BookingConfidence.MEDIUM
    if data_confidence_score >= 0.65 and source_count >= 2:
        return BookingConfidence.MEDIUM
    return BookingConfidence.LOW
