from __future__ import annotations

from typing import List, Tuple

from config import DECAY_DIGEST_CUTOFF, DECAY_INSTANT_CUTOFF
from storage.models import AlertTier, Trip
from utils.logging_config import get_logger
from utils.timeutil import utcnow

log = get_logger(__name__)


def apply_time_decay(trips: List[Trip]) -> Tuple[List[Trip], List[Trip], List[Trip]]:
    """
    Apply time decay model.

    Returns (instant_eligible, digest_eligible, archive_only).

    Rules:
    - 0–6h since scrape → instant alert eligible
    - 6–24h → digest only
    - >24h → archive only
    """
    instant: List[Trip] = []
    digest: List[Trip] = []
    archive: List[Trip] = []

    now = utcnow()

    for trip in trips:
        # Age is based on the outbound flight scrape time, not trip creation
        if trip.outbound_flight and trip.outbound_flight.scraped_at:
            age_hours = (now - trip.outbound_flight.scraped_at).total_seconds() / 3600
        else:
            age_hours = (now - trip.created_at).total_seconds() / 3600

        if age_hours <= DECAY_INSTANT_CUTOFF:
            trip.alert_tier = AlertTier.INSTANT
            instant.append(trip)
        elif age_hours <= DECAY_DIGEST_CUTOFF:
            trip.alert_tier = AlertTier.DIGEST
            digest.append(trip)
        else:
            trip.alert_tier = AlertTier.ARCHIVE
            archive.append(trip)

    log.info(
        "time_decay_results",
        instant=len(instant),
        digest=len(digest),
        archive=len(archive),
    )
    return instant, digest, archive
