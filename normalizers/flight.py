from __future__ import annotations

from typing import List

from normalizers.currency import get_converter
from storage.models import FlightLeg, RawFlightResult
from utils.confidence import compute_flight_confidence
from utils.logging_config import get_logger

log = get_logger(__name__)


async def normalize_flights(raw: List[RawFlightResult]) -> List[FlightLeg]:
    """
    Convert raw flight results to normalized FlightLeg objects with EUR prices.
    Groups results by (origin, dest, date) to compute confidence scores.
    """
    converter = get_converter()
    await converter.ensure_fresh()

    # Group by route+date for confidence scoring
    groups: dict[str, List[RawFlightResult]] = {}
    for r in raw:
        key = f"{r.origin}_{r.destination}_{r.departure_date}"
        groups.setdefault(key, []).append(r)

    legs: List[FlightLeg] = []
    for key, group in groups.items():
        # Use the lowest price from the group (most optimistic booking)
        best = min(group, key=lambda x: converter.to_eur(x.price, x.currency))
        confidence = compute_flight_confidence(group)
        price_eur = converter.to_eur(best.price, best.currency)

        if price_eur <= 0:
            continue

        try:
            leg = FlightLeg(
                origin=best.origin,
                destination=best.destination,
                price_eur=price_eur,
                departure_date=best.departure_date,
                return_date=best.return_date,
                airline=best.airline,
                booking_url=best.booking_url,
                source=best.source,
                data_confidence_score=confidence,
                scraped_at=best.scraped_at,
                raw_currency=best.currency,
                raw_price=best.price,
                travel_window=best.travel_window,
                is_error_fare_hint=any(r.is_error_fare_hint for r in group),
            )
            legs.append(leg)
        except Exception as exc:
            log.warning("flight_normalize_error", key=key, error=str(exc))

    log.info("flights_normalized", raw_count=len(raw), normalized_count=len(legs))
    return legs
