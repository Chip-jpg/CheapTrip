"""
Deal deduplication.

Two trips are the same deal when they share a fingerprint (origin city,
destination city, kind of deal, trip-length profile) and depart within
DEDUP_DATE_TOLERANCE_DAYS of each other: a 2-night and a 4-night Krakow
weekend two days apart are one "weekend in Krakow" deal. The exact price is not part of it:
a fare that moves by €1 between cycles is not a new deal.

- Within a cycle, similar trips collapse into the cheapest; the same
  itinerary found by several sources keeps one trip listing every source.
- Across cycles, a trip similar to one saved in the last DEDUP_WINDOW_DAYS
  is a duplicate unless it is at least DEDUP_PRICE_TOLERANCE_PCT cheaper.
  Then it is a new deal ("price dropped from €X") and older unsent copies
  are archived, so only the better price can alert.
- One exception: a deal that qualifies for an instant alert for the first
  time (no similar deal was ever instant or sent) is offered again even at
  the same price. Fares seen on a cold start land in the digest because
  there is no history yet; once history shows them to be unusually cheap,
  they should alert once.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from config import DEDUP_DATE_TOLERANCE_DAYS, DEDUP_PRICE_TOLERANCE_PCT, DEDUP_WINDOW_DAYS
from storage.database import (
    DedupKey,
    SimilarDeal,
    archive_unsent,
    find_similar_deals,
    is_duplicate,
    promote_deal,
    save_deal,
)
from storage.models import AlertTier, DealType, Trip
from trip_builder.feasibility import get_trip_profile
from utils.airport_clusters import cluster_city_code
from utils.confidence import compute_booking_confidence
from utils.logging_config import get_logger

log = get_logger(__name__)


def _city(code: str) -> str:
    return cluster_city_code(code) or code


def fingerprint(trip: Trip) -> DedupKey:
    """(origin city, destination city, kind, length profile or '') — not price or exact dates."""
    leg = trip.outbound_flight
    origin = _city(leg.origin) if leg else ""
    if leg:
        dest = _city(leg.destination)
    else:
        dest = (trip.hotel.location if trip.hotel else trip.route).lower()

    deal_type = DealType(trip.deal_type)
    if deal_type == DealType.COMPLETE_TRIP and trip.hotel:
        kind = f"stay:{trip.hotel.name.lower()}"
    elif deal_type == DealType.HOTEL_ONLY and trip.hotel:
        kind = f"hotel:{trip.hotel.name.lower()}"
    elif deal_type == DealType.PACKAGE:
        kind = f"package:{(leg.package_hotel or '').lower() if leg else ''}"
    elif deal_type == DealType.REPOSITIONED and trip.repositioning_legs:
        kind = f"via:{trip.repositioning_legs[0].hub}"
    else:
        kind = "flight"  # flight-only deals and error fares
    profile = trip.trip_length_profile or get_trip_profile(trip.nights)
    return origin, dest, kind, profile.value if profile else ""


def _close(a: Optional[date], b: Optional[date]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs((a - b).days) <= DEDUP_DATE_TOLERANCE_DAYS


def _merge_sources(kept: Trip, other: Trip) -> None:
    new = [s for s in other.source_list if s not in kept.source_list]
    if not new:
        return
    kept.source_list = kept.source_list + new
    has_url = bool(
        (kept.outbound_flight and kept.outbound_flight.booking_url)
        or (kept.hotel and kept.hotel.booking_url)
    )
    kept.booking_confidence = compute_booking_confidence(
        data_confidence_score=kept.data_confidence_score,
        source_count=len(kept.source_list),
        has_booking_url=has_url,
        is_error_fare=kept.is_error_fare,
    )


def collapse_similar(trips: List[Trip]) -> List[Trip]:
    """One trip per fingerprint and ±DEDUP_DATE_TOLERANCE_DAYS window: the cheapest."""
    kept_by_key: Dict[DedupKey, List[Trip]] = {}
    kept: List[Trip] = []
    for trip in sorted(trips, key=lambda t: t.total_cost_eur):
        key = fingerprint(trip)
        match = next((k for k in kept_by_key.get(key, []) if _close(k.departure_date, trip.departure_date)), None)
        if match is None:
            kept_by_key.setdefault(key, []).append(trip)
            kept.append(trip)
        elif match.departure_date == trip.departure_date and match.return_date == trip.return_date:
            _merge_sources(match, trip)  # the same itinerary seen by another source
    log.info("similar_trips_collapsed", before=len(trips), after=len(kept))
    return kept


async def _similar_saved(trip: Trip, key: DedupKey) -> List[SimilarDeal]:
    """Similar deals saved within the window, departing within the date tolerance."""
    since = datetime.utcnow() - timedelta(days=DEDUP_WINDOW_DAYS)
    return [d for d in await find_similar_deals(key, since) if _close(d.depart_date, trip.departure_date)]


def _first_time_instant(trip: Trip, similar: List[SimilarDeal]) -> bool:
    return AlertTier(trip.alert_tier) == AlertTier.INSTANT and not any(
        d.alert_tier == AlertTier.INSTANT.value or d.is_alerted for d in similar
    )


async def deduplicate_trips(trips: List[Trip]) -> Tuple[List[Trip], List[Trip]]:
    """
    Filter trips against the persistent dedup store.

    Returns (new_trips, duplicate_trips).
    New trips are also persisted to the database.
    """
    new_trips: List[Trip] = []
    dupes: List[Trip] = []
    seen_hashes: set[str] = set()

    for trip in trips:
        if not trip.hash:
            trip.hash = trip.compute_hash()

        if trip.hash in seen_hashes:
            dupes.append(trip)
            continue
        seen_hashes.add(trip.hash)

        key = fingerprint(trip)
        similar = await _similar_saved(trip, key)
        if similar:
            previous = min(d.total_cost for d in similar)
            if trip.total_cost_eur <= previous * (1 - DEDUP_PRICE_TOLERANCE_PCT):
                trip.previous_price_eur = previous
                trip.alert_reasons = trip.alert_reasons + [f"price dropped from €{previous:.0f}"]
            elif _first_time_instant(trip, similar):
                trip.alert_reasons = trip.alert_reasons + ["now unusually cheap for this route"]
            else:
                dupes.append(trip)
                log.debug("dedup_similar_skipped", route=trip.route, price=trip.total_cost_eur, previous=previous)
                continue
            await archive_unsent([d.hash for d in similar if d.hash != trip.hash])
            if any(d.hash == trip.hash for d in similar):
                # The very same fare is already saved (unsent digest): offer it as instant now
                if await promote_deal(trip):
                    new_trips.append(trip)
                else:
                    dupes.append(trip)
                continue
        elif await is_duplicate(trip.hash):  # rows saved before the dedup key existed
            dupes.append(trip)
            continue

        if await save_deal(trip, key):
            new_trips.append(trip)
        else:
            dupes.append(trip)

    log.info(
        "dedup_results",
        new=len(new_trips),
        duplicates=len(dupes),
        batch_size=len(trips),
    )
    return new_trips, dupes
