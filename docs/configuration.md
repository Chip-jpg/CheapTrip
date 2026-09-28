# Configuration Reference

## Environment variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes* | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes* | Your Telegram chat/group ID |
| `ANTHROPIC_API_KEY` | No | Enables AI message polishing |
| `DATABASE_URL` | No | SQLite path (default: `sqlite+aiosqlite:///./data/travel_deals.db`) |
| `SCRAPE_INTERVAL_MINUTES` | No | Scrape cycle frequency (default: 30) |
| `DIGEST_HOUR` | No | Daily digest send hour in Europe/Rome TZ (default: 8) |
| `DIGEST_MINUTE` | No | Digest minute (default: 0) |
| `EUROPE_TRIP_MAX_EUR` | No | Backstop: short-haul trips at or under this always alert instantly (default: 25) |
| `LONGHAUL_TRIP_MAX_EUR` | No | Backstop: long-haul trips at or under this always alert instantly (default: 250) |
| `FLIGHT_DISCOUNT_MIN_PCT` | No | Min discount % for flight instant alert (default: 60) |
| `HOTEL_DISCOUNT_MIN_PCT` | No | Min discount % for hotel instant alert (default: 60) |

*Without Telegram credentials, the engine runs in dry-run mode (logs alerts but doesn't send).

## User preferences (`config/user_preferences.yaml`)

```yaml
# Airports you can depart from. Cluster-expanded automatically.
# Milan cluster = MXP + LIN + BGY
home_airports:
  - MXP
  - LIN
  - BGY

# Allow multi-leg repositioning deals (cheap to hub, then long-haul from hub)
allow_repositioning: true

# Maximum total trip budget in EUR. null = no limit.
max_trip_budget: null

# Destinations to never show (IATA codes)
excluded_destinations: []

# Trip length categories to search for
# Options: weekend (2-4 nights), short (4-7), medium (7-14), long (14-30)
preferred_trip_lengths:
  - weekend
  - short
  - medium

# Minimum acceptable hotel rating (0-10 scale, Booking.com style)
# Hotels below this are filtered unless they have exceptional discounts
minimum_hotel_rating: 7.0

# Preferred hotel rating — used in deal scoring
preferred_hotel_rating: 8.0

# Hotels below minimum_hotel_rating still pass if total trip cost ≤ this (EUR)
hotel_exceptionally_low_trip_cost: 80.0

# Days ahead to search for departures
search_window_days: 90
```

## Settings class fields (advanced)

These can be overridden via environment variables (uppercase with underscores):

| Setting | Default | Description |
|---------|---------|-------------|
| `minimum_hotel_rating` | 7.0 | Hotel quality floor |
| `preferred_hotel_rating` | 8.0 | Preferred quality level |
| `hotel_low_rating_max_discount` | 70.0 | Discount % that overrides low rating |
| `price_anomaly_min_drop_pct` | 35.0 | % below the route's usual price (same trip length and month, earlier cycles) = anomaly |
| `booking_confidence_min_for_instant` | MEDIUM | Min booking confidence for instant alert |
| `search_window_days` | 90 | Default search horizon in days |
