"""
Alert policy — the single place that decides a trip's alert tier.

A trip becomes INSTANT when at least one promotion rule fires and no gate
blocks it; otherwise it goes to the DIGEST. Every decision carries
human-readable reasons, stored on the trip for logs and alert messages.

Promotion rules (any):
  - destination is on the user's priority list (bypasses price)
  - possible error fare
  - historical price anomaly
  - price at or under the short-haul / long-haul backstop (exceptional fares)
  - discount at or above the flight / hotel discount threshold

Gates (all must pass for INSTANT):
  - the itinerary is feasible
  - booking confidence ≥ BOOKING_CONFIDENCE_MIN_FOR_INSTANT
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from config import Settings, get_settings
from preferences import UserPreferences, get_preferences
from storage.models import AlertTier, BookingConfidence, DealType, Trip
from utils import airports
from utils.logging_config import get_logger

log = get_logger(__name__)

_CONFIDENCE_RANK = {
    BookingConfidence.LOW: 0,
    BookingConfidence.MEDIUM: 1,
    BookingConfidence.HIGH: 2,
}


@dataclass
class TierDecision:
    tier: AlertTier
    reasons: List[str] = field(default_factory=list)


def is_short_haul_trip(trip: Trip) -> bool:
    """Short/medium-haul trips use the Europe price threshold."""
    if trip.outbound_flight:
        return not airports.is_long_haul(trip.outbound_flight.destination, trip.outbound_flight.origin)
    if trip.hotel:
        dest = airports.iata_for_city(trip.hotel.location.split(",")[0])
        return bool(dest) and not airports.is_long_haul(dest)
    return False


def _min_instant_confidence(settings: Settings) -> BookingConfidence:
    raw = (settings.booking_confidence_min_for_instant or "").strip().upper()
    try:
        return BookingConfidence(raw)
    except ValueError:
        log.warning("invalid_booking_confidence_min", value=raw, fallback="MEDIUM")
        return BookingConfidence.MEDIUM


def _promotion_reasons(trip: Trip, settings: Settings, prefs: UserPreferences) -> List[str]:
    reasons: List[str] = []
    dest = trip.outbound_flight.destination if trip.outbound_flight else ""

    if dest and dest in prefs.priority_destinations:
        reasons.append(f"priority destination {dest}")
    if trip.deal_type == DealType.PACKAGE:
        return reasons  # packages have no price history to judge: instant only for priority destinations
    if trip.is_error_fare:
        reasons.append("possible error fare")
    if trip.is_anomaly:
        reasons.append(f"historical anomaly: {trip.anomaly_description or 'price well below history'}")

    short_haul = is_short_haul_trip(trip)
    threshold = settings.europe_trip_max_eur if short_haul else settings.longhaul_trip_max_eur
    # "From" prices of undated feed posts aren't bookable fares: backstop only dated trips
    if trip.departure_date and trip.total_cost_eur <= threshold:
        label = "short-haul" if short_haul else "long-haul"
        reasons.append(f"at or under the €{threshold:.0f} {label} backstop price")

    discount = trip.discount_pct or 0.0
    if trip.deal_type == DealType.FLIGHT_ONLY and discount >= settings.flight_discount_min_pct:
        reasons.append(f"{discount:.0f}% below normal flight price")
    if trip.deal_type == DealType.HOTEL_ONLY and discount >= settings.hotel_discount_min_pct:
        reasons.append(f"{discount:.0f}% below normal hotel price")
    return reasons


def decide_tier(
    trip: Trip,
    settings: Optional[Settings] = None,
    prefs: Optional[UserPreferences] = None,
) -> TierDecision:
    settings = settings or get_settings()
    prefs = prefs or get_preferences()

    reasons = _promotion_reasons(trip, settings, prefs)
    if not reasons:
        return TierDecision(AlertTier.DIGEST, ["no instant criteria met"])

    if not trip.is_feasible:
        notes = "; ".join(trip.feasibility_notes) or "itinerary not feasible"
        return TierDecision(AlertTier.DIGEST, reasons + [f"held back: {notes}"])

    minimum = _min_instant_confidence(settings)
    confidence = BookingConfidence(trip.booking_confidence)
    if _CONFIDENCE_RANK[confidence] < _CONFIDENCE_RANK[minimum]:
        return TierDecision(
            AlertTier.DIGEST,
            reasons + [f"held back: booking confidence {confidence.value} below {minimum.value}"],
        )

    return TierDecision(AlertTier.INSTANT, reasons)


def apply_decision(trip: Trip, decision: TierDecision) -> None:
    trip.alert_tier = decision.tier
    trip.alert_reasons = list(decision.reasons)
