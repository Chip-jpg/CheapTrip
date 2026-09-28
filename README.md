# CheapTrip — Travel Deal Intelligence Engine

A self-hosted bot that monitors global flight and hotel prices around the clock, detects historically low deals, assembles complete itineraries, and sends you a Telegram alert before the price disappears.

It runs as a background service (or a cron-style single cycle) and requires no browser or manual searching. You configure your home airports, budget, and preferred trip lengths once — then it handles everything else.

---

## How it works

Every 90 minutes the engine runs a full pipeline:

```
User Preferences (YAML)
        │
        ▼
Date Discovery ──► search windows for each trip-length profile
        │
        ▼
Scrapers (concurrent)
  ├── Skyscanner (RapidAPI)
  ├── Google Flights
  ├── Secret Flying
  ├── Holiday Pirates
  ├── Going.com
  ├── Booking.com
  └── (each airport cluster-expanded automatically)
        │
        ▼
Normalizers — prices → EUR, data confidence score
        │
        ▼
Trip Builder
  ├── Flight-only deals
  ├── Complete trips  (flight + hotel matched by destination)
  └── Repositioned routes  (BGY → LHR → NYC when cheaper than direct)
        │  each trip: feasibility check, category, anomaly detection
        ▼
Hard Filters — price thresholds, hotel quality, budget cap, excluded destinations
        │
        ▼
Time Decay — fresh deals stay INSTANT, older ones downgrade to DIGEST
        │
        ▼
Deduplication — SHA-256 hash check against database
        │
        ▼
Telegram
  ├── Instant alerts  (up to 5/hour, rate-limited)
  └── Daily digest    (top deals at 08:00 each morning)
```

### Deal types

| Type | Description |
|------|-------------|
| **Flight only** | Just the outbound (and optionally return) flight |
| **Complete trip** | Flight + hotel matched to the same destination |
| **Repositioned** | Multi-leg route through a cheaper hub (e.g. Milan → London → New York when the London leg is on sale) |

### Alert tiers

| Tier | When it fires |
|------|--------------|
| **Instant** | Deal is < 6 hours old, price is below threshold, confidence is HIGH or MEDIUM |
| **Digest** | Collected into the 08:00 daily summary |
| **Archive** | Deal is > 24 hours old — stored in DB but never alerted |

### Deal categories

The engine classifies every deal before alerting: Weekend Escape, Budget City Break, Beach Holiday, Long-Haul Adventure, Luxury Discount, Error Fare, Package Arbitrage, Hotel Steal, Flight Steal.

### Historical anomaly detection

Every price observation is stored. The engine computes 30/90-day averages and standard deviations per route. A deal is flagged as an anomaly if it is more than 2σ below the 30-day average, is an all-time low, or is more than 30% below the recent median.

---

## Prerequisites

Before you install the engine, collect these credentials:

| Credential | Required | Where to get it |
|------------|----------|-----------------|
| Telegram bot token | **Yes** | [@BotFather](https://t.me/BotFather) on Telegram |
| Telegram chat ID | **Yes** | One-time API call described below |
| Anthropic API key | Recommended | [console.anthropic.com](https://console.anthropic.com) — used only to format alert messages |
| RapidAPI key (Skyscanner) | Optional | [rapidapi.com](https://rapidapi.com/search/skyscanner) — enables live Skyscanner data |
| ExchangeRate API key | Optional | [exchangerate-api.com](https://www.exchangerate-api.com) — for live currency conversion (static fallback rates are built in) |

The engine runs without RapidAPI and ExchangeRate keys — it falls back to free scrapers and static exchange rates. Telegram + Anthropic are needed to receive formatted alerts.

---

## Installation

### Option A — Run locally (Python 3.11+)

**1. Clone the repository**

```bash
git clone https://github.com/chip-jpg/cheaptrip-by-claude.git
cd cheaptrip-by-claude
```

**2. Create a virtual environment**

```bash
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate.bat     # Windows Command Prompt
# .venv\Scripts\Activate.ps1     # Windows PowerShell
```

**3. Install dependencies**

```bash
pip install -r requirements.txt
```

On Linux you may need system packages for `lxml`:

```bash
sudo apt-get install gcc libxml2-dev libxslt-dev python3-dev
```

**4. Copy and fill in the environment file**

```bash
cp .env.example .env
```

Open `.env` in any editor and fill in your credentials:

```ini
TELEGRAM_BOT_TOKEN=123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321

ANTHROPIC_API_KEY=sk-ant-...

# Optional — remove lines you don't have keys for
RAPIDAPI_KEY=your_key_here
EXCHANGE_RATE_API_KEY=your_key_here
```

**5. Configure your preferences** (optional — defaults work out of the box)

```bash
cp config/user_preferences.yaml.example config/user_preferences.yaml
# (or edit config/user_preferences.yaml directly — it ships with defaults)
```

The most important settings to personalise:

```yaml
home_airports:         # airports you actually fly from
  - MXP
  - LIN
  - BGY

preferred_trip_lengths:
  - weekend            # 2–4 nights
  - short              # 4–7 nights
  - medium             # 7–14 nights
  # - long             # 14–30 nights

max_trip_budget: null  # set to e.g. 400 to cap at €400 total
```

**6. Verify the setup with a single cycle**

```bash
python main.py cycle
```

If everything is configured correctly, the engine will scrape, build trips, apply filters, and log results. If Telegram is set up, you should receive a startup message in your chat.

**7. Start the continuous engine**

```bash
python main.py run
```

The engine runs indefinitely, scraping every 90 minutes and sending a digest at 08:00. Press `Ctrl+C` to stop.

---

### Option B — Docker Compose

**1. Clone and configure (same steps 1 and 4 above)**

**2. Build and start**

```bash
docker-compose up -d
```

**3. Tail the logs**

```bash
docker-compose logs -f
```

**4. Stop**

```bash
docker-compose down
```

The `data/`, `logs/`, and `config/` directories are mounted as volumes so your database and preferences survive container restarts.

---

## Telegram setup

### Step 1 — Create a bot

1. Open Telegram and search for **@BotFather**
2. Send the message `/newbot`
3. BotFather asks for a display name (e.g. `My Deal Bot`) and a username ending in `bot` (e.g. `mydeals_bot`)
4. BotFather replies with a token like `123456789:AAFxxx...` — copy it to `TELEGRAM_BOT_TOKEN` in `.env`

### Step 2 — Get your chat ID

1. Open the bot you just created and send any message (e.g. `/start`)
2. In your browser, open (replace `YOUR_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
3. Find `"chat":{"id":XXXXXXX}` in the JSON response — that number is your chat ID
4. Copy it to `TELEGRAM_CHAT_ID` in `.env`

> **Group chat:** Add the bot to a group, send a message there, then run `getUpdates`. The group chat ID is a negative number like `-1001234567890`.

### Step 3 — Test

```bash
python main.py cycle
```

You should receive a message in your Telegram chat within a few seconds.

---

## CLI commands

```bash
# Start the 24/7 engine (scrapes every 90 min, digest at 08:00)
python main.py run

# Run one scrape-filter-alert cycle and exit
python main.py cycle

# Send today's best deals as a digest right now
python main.py digest

# Print database stats (deal counts, price history, top routes)
python main.py status

# Show scraper health (OK / DEGRADED / FAILING per source)
python main.py health

# Search a specific route interactively
python main.py search --origin MXP --dest NRT --days 30 --nights 7
```

---

## Configuration reference

### `config/user_preferences.yaml`

| Key | Default | Description |
|-----|---------|-------------|
| `home_airports` | `[MXP, LIN, BGY]` | IATA codes of your departure airports. Each is expanded to its full city cluster (Milan → MXP + LIN + BGY). |
| `allow_repositioning` | `true` | Search for cheaper multi-leg routes via a hub airport. |
| `max_trip_budget` | `null` | Maximum total trip cost in EUR. `null` means no cap. |
| `excluded_destinations` | `[]` | Airport codes to never include in results (e.g. `["DXB", "LAS"]`). |
| `priority_destinations` | `[]` | Airport codes that always trigger an INSTANT alert regardless of price (your dream destinations). |
| `preferred_trip_lengths` | `[weekend, short, medium]` | Which trip profiles to search. Options: `weekend` (2–4 nights), `short` (4–7), `medium` (7–14), `long` (14–30). |
| `minimum_hotel_rating` | `7.0` | Hotels below this score (out of 10) are filtered unless heavily discounted. |
| `preferred_hotel_rating` | `8.0` | Threshold for "Luxury Discount" category. |
| `search_window_days` | `90` | How many days ahead to search for departure dates. |
| `min_hotel_review_count` | `null` | Minimum number of reviews a hotel must have. |

### `.env` — alert thresholds

| Variable | Default | Description |
|----------|---------|-------------|
| `EUROPE_TRIP_MAX_EUR` | `120` | Maximum price for a Europe trip to qualify as INSTANT. |
| `LONGHAUL_TRIP_MAX_EUR` | `450` | Maximum price for a long-haul trip to qualify as INSTANT. |
| `HOTEL_DISCOUNT_MIN_PCT` | `60` | Minimum discount % for a hotel deal to qualify as INSTANT. |
| `FLIGHT_DISCOUNT_MIN_PCT` | `60` | Minimum discount % for a flight deal to qualify as INSTANT. |
| `INSTANT_ALERTS_PER_HOUR` | `5` | Maximum Telegram alerts per hour. |
| `SCRAPE_INTERVAL_MINUTES` | `90` | How often the scraping cycle runs. |
| `DIGEST_HOUR` / `DIGEST_MINUTE` | `8` / `0` | Time (Europe/Rome) to send the daily digest. |

---

## Project structure

```
cheaptrip-by-claude/
├── main.py                       # CLI entry point (run / cycle / digest / status / health / search)
├── config.py                     # Settings model, airport lists, threshold constants
├── preferences.py                # UserPreferences model + YAML loader
│
├── config/
│   └── user_preferences.yaml     # Your editable preferences
│
├── scrapers/                     # Data collection layer
│   ├── aggregator.py             # Runs all scrapers concurrently, cluster-expands airports
│   ├── health_monitor.py         # Tracks consecutive failures per scraper
│   ├── skyscanner.py             # Skyscanner via RapidAPI
│   ├── google_flights.py         # Google Flights HTML scraper
│   ├── secret_flying.py          # Secret Flying deal posts
│   ├── holiday_pirates.py        # Holiday Pirates deal posts
│   ├── going.py                  # Going.com (formerly Scott's Cheap Flights)
│   └── booking_com.py            # Booking.com hotel scraper
│
├── normalizers/                  # Raw → clean data
│   ├── flight.py                 # RawFlightResult → FlightLeg (EUR, confidence score)
│   ├── hotel.py                  # RawHotelResult → HotelDeal (EUR, quality flag)
│   └── currency.py               # Live + fallback currency conversion
│
├── trip_builder/
│   ├── builder.py                # Assembles FlightLegs + HotelDeals into Trip objects
│   ├── date_discovery.py         # Generates ScraperParams for each trip-length profile
│   ├── feasibility.py            # Rejects impractical itineraries (travel time vs stay length)
│   ├── categorizer.py            # Assigns DealCategory (Weekend Escape, Beach Holiday, …)
│   ├── repositioning.py          # Finds hub-routing savings opportunities
│   └── cost_calculator.py        # Cost breakdown and discount percentage
│
├── filters/
│   ├── hard_filters.py           # Price gates, hotel quality, feasibility, budget cap
│   ├── time_decay.py             # Downgrades stale deals (6h → DIGEST, 24h → ARCHIVE)
│   └── deduplication.py          # SHA-256 hash check against database
│
├── storage/
│   ├── models.py                 # All Pydantic models (Trip, FlightLeg, HotelDeal, …)
│   ├── database.py               # Async SQLite operations (aiosqlite)
│   └── price_analytics.py        # Historical stats — avg, std dev, all-time low per route
│
├── ai_layer/
│   └── formatter.py              # Claude Haiku formats alert text (never computes prices)
│
├── notifier/
│   ├── telegram.py               # Sends instant alerts and digests via Bot API
│   └── rate_limiter.py           # Token-bucket limiter, state persisted across restarts
│
├── scheduler/
│   └── runner.py                 # APScheduler — wires together the full pipeline
│
├── utils/
│   ├── airport_clusters.py       # City-cluster expansion (MXP → MXP + LIN + BGY)
│   ├── confidence.py             # data_confidence_score computation
│   ├── retry.py                  # Async exponential-backoff retry decorator
│   └── logging_config.py         # structlog setup
│
├── tests/                        # pytest test suite (~130 tests)
├── Dockerfile
├── docker-compose.yml
└── docs/
    ├── architecture.md
    ├── configuration.md
    ├── deployment.md
    └── telegram_setup.md
```

---

## Running the tests

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

All tests are self-contained and use an isolated temporary database — no real API calls are made. CI runs the same lint and test steps on every push and pull request to `main`.

---

## Frequently asked questions

**Do I need all the API keys?**
No. The minimum viable setup is a Telegram bot token + chat ID. The engine will use free scrapers (Secret Flying, Holiday Pirates, Going.com, Google Flights) and static exchange rates. You'll get fewer flights but the pipeline still works end-to-end.

**How do I stop getting alerts for a destination I don't care about?**
Add its IATA code to `excluded_destinations` in `config/user_preferences.yaml`.

**I want to always be alerted for Tokyo / New York / wherever.**
Add the airport code to `priority_destinations`. Those destinations trigger INSTANT alerts regardless of price.

**How is the discount percentage calculated?**
Against historical data — the engine stores every price it sees and computes 30/90-day averages per route. If there's no history yet (first few days), discount is compared to the scraper's own `normal_price` field when available.

**Can it alert a group of friends?**
Yes — use a Telegram group chat ID (negative number). Add the bot to the group and use that ID in `TELEGRAM_CHAT_ID`.

**The engine found no trips. What's wrong?**
Run `python main.py health` to see if any scrapers are failing. Check `logs/engine.log` for details. The most common cause is a missing or expired RapidAPI key for the Skyscanner scraper.

**How do I change the alert thresholds?**
Edit the `EUROPE_TRIP_MAX_EUR` and `LONGHAUL_TRIP_MAX_EUR` values in `.env`. Cheaper threshold = fewer but better deals. Higher threshold = more alerts.

---

## Notes on scraper reliability

Web scrapers break when sites change their HTML structure. The engine has a health monitor that tracks consecutive failures per source and logs warnings when a scraper degrades. Check `python main.py health` regularly. The Skyscanner RapidAPI integration is the most stable source; HTML scrapers are best-effort.

---

## Docs

- [Architecture deep-dive](docs/architecture.md)
- [Full configuration reference](docs/configuration.md)
- [Deployment guide (VPS / systemd)](docs/deployment.md)
- [Telegram bot setup](docs/telegram_setup.md)
