"""
Price history and anomaly detection.

Every dated search fare is recorded under a key: origin city, destination
city, round trip or one way, stay-length bucket (the trip profile) and
departure month, e.g. "Milan → Krakow, weekend round trips, November".
A fare is only ever compared with the same key's prices from *earlier*
cycles, so a cycle can never be its own baseline.

Each fare (route plus departure and return date) counts once in a baseline,
at its latest price: a fare seen in every cycle, or by two sources, is still
one fare, so dates that happen to be searched more often can't skew the usual
price. A key has a baseline once it holds at least MIN_SAMPLES different fares,
seen over at least MIN_CYCLES earlier cycles within LOOKBACK_DAYS. A fare is
an anomaly when it is PRICE_ANOMALY_MIN_DROP_PCT below the baseline median, or
a new low (NEW_LOW_MARGIN under the cheapest of those fares). Deal-feed posts
and undated fares are never recorded or judged.

"Possible error fare" asks for more: a baseline of at least
ERROR_FARE_MIN_SAMPLES different fares, recorded on ERROR_FARE_MIN_DAYS different days,
and a fare at most ERROR_FARE_MAX_RATIO of its median and at least
ERROR_FARE_MIN_SAVING_EUR under it. A few hours of history, or a short-haul
fare that is merely cheap (€30 against a usual €79), doesn't qualify.
"""
from __future__ import annotations

import statistics
from dataclasses import astuple, dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, NamedTuple, Optional, Set

from config import get_settings
from storage.database import load_price_history, record_prices
from storage.models import FlightLeg
from trip_builder.feasibility import get_trip_profile
from utils.airport_clusters import cluster_city_code
from utils.logging_config import get_logger
from utils.timeutil import utcnow

log = get_logger(__name__)

LOOKBACK_DAYS = 30
MIN_SAMPLES = 6
MIN_CYCLES = 2
NEW_LOW_MARGIN = 0.05

ERROR_FARE_MAX_RATIO = 0.35
ERROR_FARE_MIN_SAVING_EUR = 100.0
ERROR_FARE_MIN_SAMPLES = 20
ERROR_FARE_MIN_DAYS = 3


@dataclass(frozen=True)
class PriceKey:
    origin_city: str
    dest_city: str
    trip_type: str      # "rt" | "ow"
    nights_bucket: str  # trip profile ("weekend", "short", ...) or "ow"
    depart_month: str   # "YYYY-MM"

    def describe(self) -> str:
        month = datetime.strptime(self.depart_month, "%Y-%m").strftime("%B")
        kind = "one-way" if self.trip_type == "ow" else f"{self.nights_bucket} trips"
        return f"{kind}, {month}"


def _city(code: str) -> str:
    return cluster_city_code(code) or code


def price_key(leg: FlightLeg) -> Optional[PriceKey]:
    """The history key for a fare; None when it doesn't belong in the history."""
    if leg.departure_date is None or leg.is_feed_deal:
        return None
    nights = leg.stay_nights
    if nights is not None:
        trip_type, bucket = "rt", get_trip_profile(nights).value
    elif leg.is_round_trip:
        return None  # a round-trip price without a return date can't be compared
    else:
        trip_type = bucket = "ow"
    return PriceKey(_city(leg.origin), _city(leg.destination), trip_type, bucket,
                    leg.departure_date.strftime("%Y-%m"))


class Observation(NamedTuple):
    key: PriceKey
    itinerary: str  # one fare: "MXP-KRK 2026-11-13 2026-11-16", however often it is seen
    price: float
    cycle_id: str
    day: str        # YYYY-MM-DD it was recorded on


@dataclass
class Baseline:
    median: float
    low: float
    samples: int  # different fares, each at its latest price
    cycles: int
    days: int     # distinct days the fares were recorded on

    @property
    def is_established(self) -> bool:
        """Enough history, spread over enough days, to call a fare a likely pricing mistake."""
        return self.samples >= ERROR_FARE_MIN_SAMPLES and self.days >= ERROR_FARE_MIN_DAYS


@dataclass
class AnomalyResult:
    is_anomaly: bool = False
    is_all_time_low: bool = False
    is_possible_error_fare: bool = False
    deviation_pct: Optional[float] = None  # vs the baseline median; negative = cheaper
    normal_price: Optional[float] = None   # the baseline median, when there is one
    description: Optional[str] = None


class PriceIndex:
    """Baselines per key, built from earlier cycles' observations."""

    def __init__(self, observations: Iterable[Observation]) -> None:
        """Observations oldest first: a fare seen again replaces its earlier price."""
        latest: Dict[PriceKey, Dict[str, float]] = {}
        cycles: Dict[PriceKey, Set[str]] = {}
        days: Dict[PriceKey, Set[str]] = {}
        for obs in observations:
            latest.setdefault(obs.key, {})[obs.itinerary] = obs.price
            cycles.setdefault(obs.key, set()).add(obs.cycle_id)
            days.setdefault(obs.key, set()).add(obs.day)
        self._baselines: Dict[PriceKey, Baseline] = {}
        for key, fares in latest.items():
            prices = list(fares.values())
            if len(prices) >= MIN_SAMPLES and len(cycles[key]) >= MIN_CYCLES:
                self._baselines[key] = Baseline(
                    statistics.median(prices), min(prices), len(prices), len(cycles[key]), len(days[key]),
                )

    @classmethod
    async def load(cls, exclude_cycle: str) -> "PriceIndex":
        since = utcnow() - timedelta(days=LOOKBACK_DAYS)
        rows = await load_price_history(since, exclude_cycle)
        index = cls(Observation(PriceKey(*row[:5]), *row[5:]) for row in rows)
        log.info("price_index_loaded", observations=len(rows), baselines=len(index._baselines))
        return index

    def baseline(self, leg: FlightLeg) -> Optional[Baseline]:
        key = price_key(leg)
        return self._baselines.get(key) if key else None

    def check(self, leg: FlightLeg) -> AnomalyResult:
        key = price_key(leg)
        base = self._baselines.get(key) if key else None
        if key is None or base is None:
            return AnomalyResult()

        deviation = (leg.price_eur - base.median) / base.median * 100
        result = AnomalyResult(deviation_pct=round(deviation, 1), normal_price=round(base.median, 2))
        reasons: List[str] = []
        if -deviation >= get_settings().price_anomaly_min_drop_pct:
            reasons.append(f"{-deviation:.0f}% below the usual €{base.median:.0f} ({key.describe()})")
        if leg.price_eur < base.low * (1 - NEW_LOW_MARGIN):
            result.is_all_time_low = True
            reasons.append(f"new low, under the previous best of €{base.low:.0f}")
        if reasons:
            result.is_anomaly = True
            result.description = "; ".join(reasons)
        result.is_possible_error_fare = (
            base.is_established
            and leg.price_eur <= base.median * ERROR_FARE_MAX_RATIO
            and base.median - leg.price_eur >= ERROR_FARE_MIN_SAVING_EUR
        )
        return result


async def record_fares(legs: Iterable[FlightLeg], cycle_id: str) -> int:
    """Add this cycle's dated search fares to the history; returns how many were recorded."""
    rows = []
    for leg in legs:
        key = price_key(leg)
        if key:
            rows.append((
                f"{leg.origin}-{leg.destination}", leg.price_eur, leg.source, *astuple(key),
                leg.departure_date.isoformat(), leg.return_date.isoformat() if leg.return_date else "",
            ))
    await record_prices(rows, cycle_id)
    return len(rows)
