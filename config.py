from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",  # also reads a .env saved with a BOM (Windows Notepad)
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    # Notifications (B39). Desktop notifications need the desktop app; Telegram needs the bot above.
    notify_desktop: bool = Field(default=True)
    notify_telegram: bool = Field(default=True)
    notify_instant: bool = Field(default=True)
    notify_digest: bool = Field(default=True)
    notify_source_problems: bool = Field(default=True)
    notify_sound: bool = Field(default=True)

    # Desktop app (B40): closing the window keeps CheapTrip running in the tray
    close_to_tray: bool = Field(default=True)
    # The app's colours: "system" (follow Windows), "light" or "dark"
    theme: str = Field(default="system")
    # The app's first-run setup wizard has been finished (it opens at the first launch until then)
    setup_done: bool = Field(default=False)

    # AI (optional alert polish; prices/dates/links are verified unchanged)
    anthropic_api_key: str = Field(default="")
    ai_model: str = Field(default="claude-haiku-4-5")
    ai_timeout_seconds: float = Field(default=15.0)

    # Skyscanner via RapidAPI
    rapidapi_key: str = Field(default="")
    rapidapi_skyscanner_host: str = Field(default="skyscanner50.p.rapidapi.com")
    # Endpoint path — check the "Endpoints" tab in your RapidAPI console if you get 404s
    rapidapi_skyscanner_endpoint: str = Field(default="/api/v1/searchFlights")
    # Opt-in: RapidAPI Skyscanner quotas are small. Calls are capped per scrape cycle,
    # and a 429 pauses the source until the next UTC midnight.
    enable_skyscanner: bool = Field(default=False)
    skyscanner_max_calls_per_cycle: int = Field(default=20)

    # Search budgets: tasks per scrape cycle for each source (see scheduler/planner.py)
    # Google throttles datacenter IPs after a handful of searches; keep this modest.
    google_flights_calls_per_cycle: int = Field(default=6)
    # After a 429 or bot-check page, leave Google Flights alone for this long
    google_flights_cooldown_hours: float = Field(default=3.0)
    ryanair_calls_per_cycle: int = Field(default=30)
    travelpayouts_calls_per_cycle: int = Field(default=30)
    booking_calls_per_cycle: int = Field(default=5)
    google_hotels_calls_per_cycle: int = Field(default=10)

    # Ryanair public fare API (no key). Market sets the site locale and point of sale.
    enable_ryanair: bool = Field(default=True)
    # Empty = derived from the home airport's country (preferences `market` wins over both)
    ryanair_market: str = Field(default="")

    # Travelpayouts / Aviasales Data API (free token: https://www.travelpayouts.com).
    # The source is disabled while the token is empty; the marker adds affiliate tracking to links.
    travelpayouts_token: str = Field(default="")
    travelpayouts_marker: str = Field(default="")

    # Hotel sources. Booking.com answers plain HTTP with an AWS WAF challenge; Google Hotels
    # can't be given dates yet (see scrapers/google_hotels.py). Both are off by default.
    enable_booking_html: bool = Field(default=False)
    enable_google_hotels: bool = Field(default=False)

    # Deal feeds behind bot protection (Cloudflare / JS SPA) — off unless you have a workaround
    enable_secret_flying: bool = Field(default=False)
    enable_going: bool = Field(default=False)
    # HolidayPirates .com is the UK edition (GBP packages from UK airports): off by default
    enable_holiday_pirates: bool = Field(default=False)
    # PiratinViaggio (Italian deal site) read by Claude: runs when ANTHROPIC_API_KEY is set
    enable_piratinviaggio: bool = Field(default=True)
    # New posts sent to Claude per cycle (each post is read once, then cached)
    ai_extraction_max_posts_per_cycle: int = Field(default=30)

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

    # Alert thresholds. The flat prices are backstops for exceptional fares:
    # most instant alerts come from prices well below a route's usual price.
    europe_trip_max_eur: float = Field(default=25.0)
    longhaul_trip_max_eur: float = Field(default=250.0)
    hotel_discount_min_pct: float = Field(default=60.0)
    flight_discount_min_pct: float = Field(default=60.0)
    instant_alerts_per_hour: int = Field(default=5)

    # Booking confidence: minimum tier (LOW / MEDIUM / HIGH) for an instant alert
    booking_confidence_min_for_instant: str = Field(default="MEDIUM")

    # Historical price analytics: a fare this far below its route's usual price is an anomaly
    price_anomaly_min_drop_pct: float = Field(default=35.0)

    # Flexible date search
    preferred_trip_lengths: List[str] = Field(default=["weekend", "short", "medium"])
    search_window_days: int = Field(default=90)

    # Logging
    log_level: str = Field(default="INFO")
    log_file: str = Field(default="./logs/engine.log")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()



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

# Primary search destinations — popular short and long haul from Europe
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
