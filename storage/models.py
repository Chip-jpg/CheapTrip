from __future__ import annotations

import hashlib
from datetime import date, datetime
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
    departure_date: date
    return_date: Optional[date] = None
    airline: Optional[str] = None
    booking_url: Optional[str] = None
    source: str = ""
    data_confidence_score: float = Field(ge=0.0, le=1.0, default=0.5)
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    raw_currency: str = "EUR"
    raw_price: float = 0.0

    @field_validator("origin", "destination")
    @classmethod
    def upper_airport_code(cls, v: str) -> str:
        return v.upper().strip()


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
    departure_date: date
    return_date: Optional[date] = None
    airline: Optional[str] = None
    booking_url: Optional[str] = None
    source: str
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    extra: Dict[str, Any] = Field(default_factory=dict)


class RawHotelResult(BaseModel):
    """Raw result from any hotel scraper before normalization."""
    name: str
    location: str
    price_per_night: float
    currency: str
    nights: int
    rating: Optional[float] = None
    stars: Optional[int] = None
    booking_url: Optional[str] = None
    source: str
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class ScraperParams(BaseModel):
    origins: List[str]
    destinations: List[str]
    departure_date_from: date
    departure_date_to: date
    nights_min: int = 2
    nights_max: int = 7
    adults: int = 1
    max_price_eur: float = 2000.0
    trip_length_profile: Optional[TripLengthProfile] = None
    flexible_dates: bool = True
