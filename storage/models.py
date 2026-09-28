from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class DealType(str, Enum):
    FLIGHT_ONLY = "flight_only"
    HOTEL_ONLY = "hotel_only"
    COMPLETE_TRIP = "complete_trip"
    REPOSITIONED = "repositioned"
    ERROR_FARE = "error_fare"


class AlertTier(str, Enum):
    INSTANT = "instant"
    DIGEST = "digest"
    ARCHIVE = "archive"


class TripLengthProfile(str, Enum):
    WEEKEND = "weekend"   # 2–4 nights
    SHORT = "short"       # 4–7 nights
    MEDIUM = "medium"     # 7–14 nights
    LONG = "long"         # 14–30 nights


class DealCategory(str, Enum):
    WEEKEND_ESCAPE = "Weekend Escape"
    BUDGET_CITY_BREAK = "Budget City Break"
    BEACH_HOLIDAY = "Beach Holiday"
    LONG_HAUL_ADVENTURE = "Long-Haul Adventure"
    LUXURY_DISCOUNT = "Luxury Discount"
    ERROR_FARE = "Error Fare"
    PACKAGE_ARBITRAGE = "Package Arbitrage"
    HOTEL_STEAL = "Hotel Steal"
    FLIGHT_STEAL = "Flight Steal"


class BookingConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FlightLeg(BaseModel):
    origin: str
    destination: str
    price_eur: float
    # None for deal-feed posts that give a travel window rather than a date
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    airline: Optional[str] = None
    booking_url: Optional[str] = None
    source: str = ""
    data_confidence_score: float = Field(ge=0.0, le=1.0, default=0.5)
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    raw_currency: str = "EUR"
    raw_price: float = 0.0
    travel_window: Optional[str] = None
    is_error_fare_hint: bool = False
    # Itinerary details when the source provides them (local times)
    departure_time: Optional[datetime] = None
    arrival_time: Optional[datetime] = None
    flight_number: Optional[str] = None
    stops: Optional[int] = None
    duration_minutes: Optional[int] = None
    # True when price_eur covers the return flight too
    is_round_trip: bool = False
    # Price from a cache of recent searches (e.g. Travelpayouts) — verify before booking
    price_is_cached: bool = False
    # From a deal feed (a post, not a search): never part of the price history
    is_feed_deal: bool = False

    @field_validator("origin", "destination")
    @classmethod
    def upper_airport_code(cls, v: str) -> str:
        return v.upper().strip()

    @property
    def stay_nights(self) -> Optional[int]:
        """Stay length of a dated round trip; None for one-way or undated fares."""
        if self.departure_date and self.return_date and self.return_date > self.departure_date:
            return (self.return_date - self.departure_date).days
        return None


class HotelDeal(BaseModel):
    name: str
    location: str
    price_per_night_eur: float
    nights: int
    total_price_eur: float
    rating: Optional[float] = None
    stars: Optional[int] = None
    review_count: Optional[int] = None
    booking_url: Optional[str] = None
    source: str = ""
    data_confidence_score: float = Field(ge=0.0, le=1.0, default=0.5)
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    raw_currency: str = "EUR"
    raw_price_per_night: float = 0.0
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    meets_quality_threshold: bool = True

    @model_validator(mode="after")
    def validate_total(self) -> "HotelDeal":
        expected = self.price_per_night_eur * self.nights
        if abs(self.total_price_eur - expected) > 0.01:
            self.total_price_eur = round(expected, 2)
        return self


class RepositioningLeg(BaseModel):
    origin: str
    hub: str
    transport_type: str  # "flight" | "train" | "bus"
    price_eur: float
    duration_hours: float
    booking_url: Optional[str] = None
    source: str = ""
    data_confidence_score: float = Field(ge=0.0, le=1.0, default=0.5)


class Trip(BaseModel):
    trip_id: str = ""
    deal_type: DealType
    route: str
    outbound_flight: Optional[FlightLeg] = None
    return_flight: Optional[FlightLeg] = None
    repositioning_legs: List[RepositioningLeg] = Field(default_factory=list)
    hotel: Optional[HotelDeal] = None

    # Cost breakdown (all EUR)
    flight_cost_eur: float = 0.0
    hotel_cost_eur: float = 0.0
    repositioning_cost_eur: float = 0.0
    fees_eur: float = 0.0
    total_cost_eur: float = 0.0

    normal_price_eur: Optional[float] = None
    discount_pct: Optional[float] = None

    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    nights: Optional[int] = None

    data_confidence_score: float = Field(ge=0.0, le=1.0, default=0.5)
    source_list: List[str] = Field(default_factory=list)

    alert_tier: AlertTier = AlertTier.ARCHIVE
    is_error_fare: bool = False
    verdict: str = "MONITOR"

    # Extended classification
    category: Optional[DealCategory] = None
    booking_confidence: BookingConfidence = BookingConfidence.LOW
    trip_length_profile: Optional[TripLengthProfile] = None

    # Feasibility
    is_feasible: bool = True
    feasibility_notes: List[str] = Field(default_factory=list)

    # Historical analytics
    avg_historical_price_eur: Optional[float] = None
    historical_deviation_pct: Optional[float] = None
    is_historical_low: bool = False
    is_anomaly: bool = False
    anomaly_description: Optional[str] = None

    # Why the alert policy chose this tier (filters.alert_policy)
    alert_reasons: List[str] = Field(default_factory=list)
    # Price of the same deal seen earlier, when this one is a real drop (filters.deduplication)
    previous_price_eur: Optional[float] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    hash: str = ""

    def compute_hash(self) -> str:
        origin = self.outbound_flight.origin if self.outbound_flight else ""
        dest = self.outbound_flight.destination if self.outbound_flight else self.route
        dep = str(self.departure_date or "")
        ret = str(self.return_date or "")
        price = f"{self.total_cost_eur:.2f}"
        raw = f"{origin}{dest}{DealType(self.deal_type).value}{price}{dep}{ret}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def compute_totals(self) -> None:
        self.total_cost_eur = round(
            self.flight_cost_eur
            + self.hotel_cost_eur
            + self.repositioning_cost_eur
            + self.fees_eur,
            2,
        )
        if self.normal_price_eur and self.normal_price_eur > 0:
            self.discount_pct = round(
                (self.normal_price_eur - self.total_cost_eur) / self.normal_price_eur * 100,
                1,
            )

    def to_breakdown_dict(self) -> Dict[str, Any]:
        return {
            "flights": self.flight_cost_eur,
            "hotel": self.hotel_cost_eur,
            "repositioning": self.repositioning_cost_eur,
            "fees": self.fees_eur,
            "total": self.total_cost_eur,
        }


class RawFlightResult(BaseModel):
    """Raw result from any flight scraper before normalization."""
    origin: str
    destination: str
    price: float
    currency: str
    # None for deal-feed posts that give a travel window rather than a date
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    airline: Optional[str] = None
    booking_url: Optional[str] = None
    source: str
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    # Deal feeds: free-text travel window ("Nov–Mar") and error-fare flag from the source
    travel_window: Optional[str] = None
    is_error_fare_hint: bool = False
    # Itinerary details when the source provides them (local times)
    departure_time: Optional[datetime] = None
    arrival_time: Optional[datetime] = None
    flight_number: Optional[str] = None
    stops: Optional[int] = None
    duration_minutes: Optional[int] = None
    # True when `price` covers the return flight too
    is_round_trip: bool = False
    # Price from a cache of recent searches (e.g. Travelpayouts) — verify before booking
    price_is_cached: bool = False
    # From a deal feed (a post, not a search): never part of the price history
    is_feed_deal: bool = False
    extra: Dict[str, Any] = Field(default_factory=dict)


class RawHotelResult(BaseModel):
    """Raw result from any hotel scraper before normalization."""
    name: str
    location: str
    price_per_night: float
    currency: str
    nights: int
    rating: Optional[float] = None  # out of 10
    stars: Optional[int] = None
    review_count: Optional[int] = None
    booking_url: Optional[str] = None
    source: str
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class SearchTask(BaseModel):
    """
    One unit of search work planned for a source (scheduler/planner.py).

    Flight tasks have an origin; `destination=None` means "anywhere" for
    sources that support it. Exact-date sources get depart_from ==
    depart_to and nights_min == nights_max. Hotel tasks leave origin empty
    and use depart_from as check-in and nights_min as the stay length.
    """
    origin: Optional[str] = None
    destination: Optional[str] = None
    depart_from: date
    depart_to: date
    nights_min: int
    nights_max: int
    profile: Optional[TripLengthProfile] = None
    priority: bool = False
    adults: int = 1

    @property
    def return_date(self) -> date:
        """Return (or check-out) date for an exact-date task: departure + nights."""
        return self.depart_from + timedelta(days=self.nights_min)
