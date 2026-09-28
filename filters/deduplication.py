from __future__ import annotations

from typing import List, Tuple

from storage.database import is_duplicate, save_deal
from storage.models import Trip
from utils.logging_config import get_logger

log = get_logger(__name__)


async def deduplicate_trips(trips: List[Trip]) -> Tuple[List[Trip], List[Trip]]:
    """
    Filter trips against the persistent dedup store.

    Returns (new_trips, duplicate_trips).
    New trips are also persisted to the database.
    """
    new_trips: List[Trip] = []
    dupes: List[Trip] = []

    # Also deduplicate within this batch (same-run dedup)
    seen_hashes: set[str] = set()

    for trip in trips:
        if not trip.hash:
            trip.hash = trip.compute_hash()

        # In-batch dedup
        if trip.hash in seen_hashes:
            dupes.append(trip)
            continue
        seen_hashes.add(trip.hash)

        # Database dedup
        if await is_duplicate(trip.hash):
            dupes.append(trip)
            log.debug("dedup_skipped", hash=trip.hash, route=trip.route)
            continue

        # New deal — persist and mark as new
        saved = await save_deal(trip)
        if saved:
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


def compute_trip_hash(trip: Trip) -> str:
    """Recompute the canonical hash for a trip."""
    return trip.compute_hash()
