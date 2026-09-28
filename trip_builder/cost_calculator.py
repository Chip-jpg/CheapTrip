from __future__ import annotations

from typing import List, Optional

from storage.models import FlightLeg, HotelDeal, RepositioningLeg


def calculate_trip_total(
    outbound: Optional[FlightLeg],
    return_flight: Optional[FlightLeg],
    hotel: Optional[HotelDeal],
    repositioning: List[RepositioningLeg],
    fees: float = 0.0,
) -> float:
    """
    Compute TOTAL_COST = flights + hotel + repositioning + fees.
    All inputs MUST be in EUR. No estimation — only real values.
    """
    total = 0.0

    if outbound:
        total += outbound.price_eur
    if return_flight:
        total += return_flight.price_eur
    if hotel:
        total += hotel.total_price_eur
    for leg in repositioning:
        total += leg.price_eur
    total += fees

    return round(total, 2)


def estimate_normal_price(
    route: str,
    median_flight: Optional[float],
    median_hotel_per_night: Optional[float],
    nights: int,
) -> Optional[float]:
    """
    Estimate normal market price from historical median data.
    Returns None if insufficient historical data.
    """
    if median_flight is None and median_hotel_per_night is None:
        return None

    total = 0.0
    if median_flight:
        total += median_flight
    if median_hotel_per_night and nights > 0:
        total += median_hotel_per_night * nights
    return round(total, 2) if total > 0 else None


def compute_discount_pct(actual: float, normal: Optional[float]) -> Optional[float]:
    if not normal or normal <= 0 or actual >= normal:
        return None
    return round((normal - actual) / normal * 100, 1)


def assign_verdict(
    total_cost: float,
    discount_pct: Optional[float],
    confidence: float,
    is_europe: bool,
) -> str:
    """
    Assign human-readable verdict based on deal quality.
    This is a deterministic rule engine — not AI.
    """
    if is_europe:
        if total_cost < 80:
            return "BOOK NOW"
        if total_cost < 120:
            return "BOOK NOW" if confidence >= 0.75 else "VERIFY & BOOK"
        if total_cost < 200:
            return "GOOD DEAL"
    else:
        if total_cost < 300:
            return "BOOK NOW"
        if total_cost < 450:
            return "BOOK NOW" if confidence >= 0.75 else "VERIFY & BOOK"
        if total_cost < 600:
            return "GOOD DEAL"

    if discount_pct and discount_pct >= 60:
        return "STRONG DEAL"
    if discount_pct and discount_pct >= 40:
        return "GOOD DEAL"
    return "MONITOR"
