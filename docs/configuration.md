# Configuration reference

Two files: `.env` (credentials and engine settings) and `config/user_preferences.yaml` (your travel preferences). Run `python main.py doctor` after changing either.

## `.env`

Start from `.env.example`. Names are case-insensitive environment variables.

### Credentials

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather. Without it (and the chat ID) alerts are only logged. |
| `TELEGRAM_CHAT_ID` | — | Chat or group that receives alerts and may send commands. |
| `ANTHROPIC_API_KEY` | — | Reads PiratinViaggio posts and polishes alert wording. |
| `AI_MODEL` | `claude-haiku-4-5` | Model for both. |
| `AI_TIMEOUT_SECONDS` | `15` | Timeout for the alert polish (post reading uses at least 30s). |
| `TRAVELPAYOUTS_TOKEN` | — | Enables Travelpayouts. `TRAVELPAYOUTS_MARKER` adds affiliate tracking to its links. |
| `EXCHANGE_RATE_API_KEY` | — | Live currency rates (static fallback rates otherwise). |
| `RAPIDAPI_KEY` | — | Skyscanner via RapidAPI, also needs `ENABLE_SKYSCANNER=true`. |

### Sources

| Variable | Default | Description |
|---|---|---|
| `ENABLE_RYANAIR` | `true` | Ryanair fare API. |
| `RYANAIR_MARKET` | empty | e.g. `it-it`, `en-gb`. Empty: the `market` preference, else your home country. |
| `RYANAIR_CALLS_PER_CYCLE` | `30` | Searches per cycle. |
| `GOOGLE_FLIGHTS_CALLS_PER_CYCLE` | `6` | Google throttles datacenter IPs; keep it small. |
| `GOOGLE_FLIGHTS_COOLDOWN_HOURS` | `3` | Pause after a 429 or bot-check page. Stored in the database, so separate `main.py cycle` runs respect it too. |
| `TRAVELPAYOUTS_CALLS_PER_CYCLE` | `30` | |
| `ENABLE_PIRATINVIAGGIO` | `true` | Italian deal posts; runs when `ANTHROPIC_API_KEY` is set and a home airport is in Italy. |
| `AI_EXTRACTION_MAX_POSTS_PER_CYCLE` | `30` | New posts sent to Claude per cycle (each post is read once). |
| `ENABLE_SKYSCANNER` | `false` | `SKYSCANNER_MAX_CALLS_PER_CYCLE` (20) caps calls; a 429 pauses it until midnight UTC. |
| `ENABLE_BOOKING_HTML` | `false` | Booking.com answers plain HTTP with a bot wall. |
| `ENABLE_GOOGLE_HOTELS` | `false` | Stay dates can't be set yet. |
| `ENABLE_SECRET_FLYING` | `false` | Behind Cloudflare. |
| `ENABLE_GOING` | `false` | JavaScript-only site. |
| `ENABLE_HOLIDAY_PIRATES` | `false` | UK edition (GBP packages from UK airports). |

### Alerts

| Variable | Default | Description |
|---|---|---|
| `PRICE_ANOMALY_MIN_DROP_PCT` | `35` | % below the route's usual price (same trip length, departures within 30 days either side, earlier cycles) that makes a fare instant. |
| `EUROPE_TRIP_MAX_EUR` | `25` | Backstop: dated short-haul trips at or under this are instant. |
| `LONGHAUL_TRIP_MAX_EUR` | `250` | Backstop for long-haul trips. |
| `FLIGHT_DISCOUNT_MIN_PCT` | `60` | Discount vs the usual price that makes a flight instant. |
| `HOTEL_DISCOUNT_MIN_PCT` | `60` | The same for hotel deals with a known usual price. |
| `BOOKING_CONFIDENCE_MIN_FOR_INSTANT` | `MEDIUM` | `LOW`, `MEDIUM` or `HIGH`. |
| `INSTANT_ALERTS_PER_HOUR` | `5` | Best discounts go first. One limit covers desktop and Telegram. |

### Notifications and the desktop app

Settings → Notifications and Settings → App in the app change these; they apply at once.

| Variable | Default | Description |
|---|---|---|
| `NOTIFY_DESKTOP` | `true` | Windows notifications (the desktop app, and `main.py ui`). |
| `NOTIFY_TELEGRAM` | `true` | Telegram, when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set. Replies to Telegram commands are sent either way. |
| `NOTIFY_INSTANT` | `true` | Deals as they are found. Off: they wait for the digest. |
| `NOTIFY_DIGEST` | `true` | The daily digest. |
| `NOTIFY_SOURCE_PROBLEMS` | `true` | A source that starts failing, or recovers. |
| `NOTIFY_SOUND` | `true` | Desktop notifications play the system sound. |
| `CLOSE_TO_TRAY` | `true` | Closing the app's window keeps CheapTrip running in the tray. Off: closing it quits. |
| `SETUP_DONE` | `false` | The app's setup wizard has been finished; until then it opens at each launch. Its **Finish** button sets it. |
| `THEME` | `system` | The app's colours: `system` follows Windows' light or dark mode; `light` or `dark` fixes one. The sun/moon button in the top bar switches it too. |

### Schedule and storage

| Variable | Default | Description |
|---|---|---|
| `SCRAPE_INTERVAL_MINUTES` | `90` | Cycle interval. The healthcheck fails after 3 intervals without a cycle. |
| `DIGEST_HOUR` / `DIGEST_MINUTE` | `8` / `0` | Daily digest time, in `TIMEZONE`. |
| `TIMEZONE` | `Europe/Rome` | IANA time zone for the digest schedule. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/travel_deals.db` | SQLite file (WAL journal). Prices are kept 45 days, 15 more than anything looks back. |
| `LOG_LEVEL` / `LOG_FILE` | `INFO` / `./logs/engine.log` | |

## `config/user_preferences.yaml`

Copy `config/user_preferences.yaml.example`. Telegram commands (`/mute`, `/priority`, `/budget`) override these without editing the file; `/status` shows the result.

| Key | Default | Description |
|---|---|---|
| `home_airports` | `[MXP, LIN, BGY]` | Any airports; each is expanded to its city (MXP → MXP, LIN, BGY). |
| `preferred_trip_lengths` | `[weekend, short, medium]` | `weekend` (≤4 nights), `short` (≤7), `medium` (≤14), `long` (≤30). |
| `search_window_days` | `90` | How far ahead to search, 14–365 days. |
| `priority_destinations` | `[]` | Always instant, whatever the price. Airport codes. |
| `excluded_destinations` | `[]` | Never shown. |
| `max_trip_budget` | `null` | Skip trips above this total (EUR per person, more than 0). |
| `adults` | `1` | Travellers for searches and booking links; prices are shown per person. |
| `market` | `null` | Airline/site market (`it-it`, `en-gb`, …); `null` = from the first home airport's country. |
| `allow_repositioning` | `true` | Search from hubs and build home → hub → destination trips. |
| `repositioning_hubs` | `[LHR, LGW, AMS, CDG, FRA, MAD, BCN, DUB]` | Hubs for repositioning. |
| `minimum_hotel_rating` | `7.0` | 0–10; lower-rated hotels only pass with a big discount or a very cheap trip. |
| `preferred_hotel_rating` | `8.0` | 0–10; "Luxury Discount" category threshold. |
| `hotel_low_rating_discount_threshold` | `70.0` | Discount % (0–100) that lets a low-rated hotel through. |
| `hotel_exceptionally_low_trip_cost` | `80.0` | Total (EUR, 0 or more) under which a low-rated hotel passes. |
| `min_hotel_review_count` | `null` | Drop hotels with fewer reviews (0 or more; unknown counts are kept). |

A value outside its range is refused when saved from the app. In the file, it is ignored with a warning in the log, and its default applies; the other settings in the file still count.
