from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    # AI
    anthropic_api_key: str = Field(default="")

    # Skyscanner via RapidAPI
    rapidapi_key: str = Field(default="")
    rapidapi_skyscanner_host: str = Field(default="skyscanner50.p.rapidapi.com")
    # Endpoint path — check the "Endpoints" tab in your RapidAPI console if you get 404s
    rapidapi_skyscanner_endpoint: str = Field(default="/api/v1/searchFlights")

    # Currency
    exchange_rate_api_key: str = Field(default="")

    # Storage
    database_url: str = Field(default="sqlite+aiosqlite:///./data/travel_deals.db")

    # Scheduler
    scrape_interval_minutes: int = Field(default=90)
    digest_hour: int = Field(default=8)
    digest_minute: int = Field(default=0)
    # IANA time zone for the daily digest schedule (DIGEST_HOUR/DIGEST_MINUTE are local to it)
    timezone: str = Field(default="Europe/Rome")

    # Alert thresholds
    europe_trip_max_eur: float = Field(default=120.0)
    longhaul_trip_max_eur: float = Field(default=450.0)
    hotel_discount_min_pct: float = Field(default=60.0)
    flight_discount_min_pct: float = Field(default=60.0)
    instant_alerts_per_hour: int = Field(default=5)

    # Hotel quality
    minimum_hotel_rating: float = Field(default=7.0)
    preferred_hotel_rating: float = Field(default=8.0)
    hotel_low_rating_max_discount: float = Field(default=70.0)

    # Booking confidence
    booking_confidence_min_for_instant: str = Field(default="MEDIUM")

    # Historical price analytics
    price_anomaly_std_dev_threshold: float = Field(default=2.0)
    price_sudden_drop_pct: float = Field(default=30.0)

    # Flexible date search
    preferred_trip_lengths: List[str] = Field(default=["weekend", "short", "medium"])
    search_window_days: int = Field(default=90)

    # Logging
    log_level: str = Field(default="INFO")
    log_file: str = Field(default="./logs/engine.log")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ── Airport layers ────────────────────────────────────────────────────────────

LAYER_1_AIRPORTS = ["MXP", "LIN", "BGY"]

LAYER_2_AIRPORTS = ["VCE", "VRN", "BLQ", "FCO", "CIA"]

LAYER_3_HUBS = ["LHR", "LGW", "AMS", "CDG", "FRA", "MAD", "BCN", "DUB"]

ALL_ORIGIN_AIRPORTS = LAYER_1_AIRPORTS + LAYER_2_AIRPORTS + LAYER_3_HUBS

# Airport clusters, cities, regions and distances live in utils/airports.py.

# ── Trip length profiles ──────────────────────────────────────────────────────

TRIP_LENGTH_NIGHTS: dict[str, tuple[int, int]] = {
    "weekend": (2, 4),
    "short":   (4, 7),
    "medium":  (7, 14),
    "long":    (14, 30),
}

# ── Feasibility constraints ───────────────────────────────────────────────────

FEASIBILITY_MAX_TRAVEL_HOURS: dict[str, float] = {
    "weekend": 8.0,
    "short":   12.0,
    "medium":  16.0,
    "long":    24.0,
}

FEASIBILITY_MIN_NIGHTS: dict[str, int] = {
    "long": 5,
}

# Primary search destinations — popular short/long haul from Italy
POPULAR_DESTINATIONS = [
    # Europe
    "KRK", "WAW", "PRG", "BUD", "LIS", "ATH", "DUB", "CPH", "ARN",
    "HEL", "OSL", "VIE", "ZRH", "BRU", "EDI", "GVA", "NCE", "MRS",
    "OPO", "SVQ", "MAH", "IBZ", "PMI", "TFS", "ACE", "LPA",
    # Long-haul
    "JFK", "EWR", "LAX", "MIA", "ORD", "BOS", "YYZ", "YVR",
    "NRT", "HND", "ICN", "HKG", "BKK", "SIN", "KUL", "CGK",
    "DXB", "AUH", "DOH", "TLV", "CAI",
    "GRU", "EZE", "BOG", "LIM", "SCL",
    "JNB", "CPT", "NBO",
    "SYD", "MEL", "AKL",
]

# Repositioning threshold constants
REPOSITIONING_MIN_SAVING_EUR = 80.0
REPOSITIONING_MIN_SAVING_PCT = 0.25
REPOSITIONING_MIN_LAYOVER_HOURS = 2.0

# Deduplication constants
DEDUP_PRICE_TOLERANCE_PCT = 0.05
DEDUP_DATE_TOLERANCE_DAYS = 2
DEDUP_WINDOW_DAYS = 7

# Time decay windows (hours)
DECAY_INSTANT_CUTOFF = 6
DECAY_DIGEST_CUTOFF = 24
