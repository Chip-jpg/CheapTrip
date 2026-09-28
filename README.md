# CheapTrip — Travel Deal Intelligence Engine

A self-hosted bot that searches cheap flights (and flight + hotel deals) from your home airports around the clock, learns what each route usually costs, and sends a Telegram alert when something is genuinely cheap.

You set your home airports, trip lengths and budget once; it runs every 90 minutes and sends a daily digest. You can steer it from Telegram (`/deals`, `/mute KRK`, `/priority JFK`, `/pause`).

---

## How it works

```
Preferences (YAML + Telegram overrides)
        │
        ▼
Search planner ── budgets per source, rotation over dates and destinations,
        │         priority destinations first, hub searches for repositioning
        ▼
Sources (concurrent)
  ├── Ryanair fare API ............ round trips "to anywhere" from each home airport
  ├── Google Flights .............. exact route + date searches (rate-limit aware)
  ├── Travelpayouts ............... cached prices "to anywhere" (needs a free token)
  ├── PiratinViaggio .............. Italian deal posts read by Claude (needs an Anthropic key)
  └── Booking link ................ every flight alert links a hotel search for its dates
        │
        ▼
Normalize ── EUR prices, confidence per fare, unknown currencies dropped
        │
        ▼
Build trips ── flight-only, packages, hotel-only, complete, repositioned (via a hub)
        │       stay length, trip profile, feasibility, category
        ▼
Price history ── each fare is compared with the same route / length / month
        │         in earlier cycles only (never with itself)
        ▼
Merge near-duplicates ── same cities, kind and length, ±2 days → the cheapest
        │
        ▼
Alert policy ── INSTANT or DIGEST, with the reasons
        │
        ▼
Dedup ── a known deal alerts again only after a ≥5% price drop
        │
        ▼
Telegram ── instant alerts (5/hour, best discount first) + daily digest at 08:00
```

### When is a deal instant?

A deal goes to the **instant** queue when at least one of these holds, and it is feasible (travel time fits the trip length) with at least MEDIUM booking confidence:

| Reason | Meaning |
|---|---|
| Priority destination | You listed it in `priority_destinations` (or `/priority` in Telegram) |
| Unusually cheap | At least `PRICE_ANOMALY_MIN_DROP_PCT` (35%) below the usual price for that route, trip length and month, or a new low. Needs at least 6 prices from 2 earlier cycles. |
| Possible error fare | Flagged by the source, or under 40% of the usual price |
| Backstop price | A dated round trip at or under €25 (Europe) / €250 (long-haul) |

Everything else goes to the **digest**. On a fresh start there is no price history, so for the first cycles almost everything goes to the digest; a fare seen then still alerts once when history later shows it is unusually cheap.

### Deal types

| Type | What it is |
|---|---|
| ✈️ Flight | A round trip from a home airport, with a Booking.com link for hotels on the same dates |
| 📦 Package | Flight + hotel sold together, from a PiratinViaggio post (price per person) |
| 🏨 Hotel | A hotel stay from a PiratinViaggio hotel post |
| 🏨+✈️ Complete trip | A flight plus a hotel from a hotel source on exactly the same dates |
| 🔀 Repositioned | Home → hub on one ticket, hub → destination on another, when it beats flying direct |

---

## Prerequisites

| Credential | Needed | Where to get it |
|---|---|---|
| Telegram bot token + chat ID | **Yes** | [@BotFather](https://t.me/BotFather); see [Telegram setup](docs/telegram_setup.md) |
| Anthropic API key | Recommended | [console.anthropic.com](https://console.anthropic.com): reads PiratinViaggio posts (Claude Haiku) and polishes alert wording. Prices are never computed by AI. |
| Travelpayouts token | Optional | [travelpayouts.com](https://www.travelpayouts.com): a second "search everywhere" flight source |
| ExchangeRate API key | Optional | Live currency rates (static fallback rates are built in) |

Ryanair and Google Flights need no key.

---

## Installation

### Option A — Python 3.11+

```bash
git clone https://github.com/Chip-jpg/CheapTrip.git
cd CheapTrip
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                   # fill in your credentials
cp config/user_preferences.yaml.example config/user_preferences.yaml   # edit your airports
python main.py doctor                                  # checks keys, Telegram, preferences, sources
python main.py cycle                                   # one search cycle
python main.py run                                     # run 24/7 (Ctrl+C to stop)
```

On Linux, `lxml` may need `sudo apt-get install gcc libxml2-dev libxslt-dev python3-dev`.

### Option B — Docker Compose

```bash
cp .env.example .env    # fill in your credentials
docker-compose up -d
docker-compose exec engine python main.py doctor
docker-compose logs -f
```

The container runs as a non-root user (uid 1000); `data/`, `logs/` and `config/` are mounted from the host. See the [deployment guide](docs/deployment.md).

### The preferences that matter most

```yaml
home_airports: [MXP, LIN, BGY]   # expanded to the whole city (Milan), any airport works
preferred_trip_lengths: [weekend, short, medium]
priority_destinations: [JFK, NRT]   # always alert, whatever the price
max_trip_budget: null               # e.g. 400
adults: 1                           # prices are always shown per person
```

All preferences: [configuration reference](docs/configuration.md).

---

## Commands

### CLI

```bash
python main.py run            # 24/7: cycle every 90 min, digest at 08:00, Telegram commands
python main.py cycle          # one cycle, then exit
python main.py digest         # send the digest now
python main.py doctor         # configuration check (exit 1 on blocking problems)
python main.py health         # per-source health (OK / DEGRADED / FAILING / DISABLED and why)
python main.py healthcheck    # exit 1 if no cycle completed recently (Docker healthcheck)
python main.py status         # database statistics
python main.py search --dest NRT --days 30 --nights 7   # one route, from your first home airport
```

### Telegram (while `main.py run` is running)

| Command | Effect |
|---|---|
| `/deals` | Top 10 deals of the last 24h, one per route |
| `/status` | Last cycle, alerts today, pause, muted / priority / budget |
| `/health` | Source health |
| `/mute KRK` · `/unmute KRK` | Never / again alert a destination |
| `/priority KRK` · `/unpriority KRK` | Always alert a destination |
| `/budget 300` · `/budget off` | Skip trips above a price |
| `/pause [hours]` · `/resume` | Hold alerts (default 24h); searching continues |

Only the chat in `TELEGRAM_CHAT_ID` is answered.

---

## Tuning alerts

- **Too many instant alerts:** raise `PRICE_ANOMALY_MIN_DROP_PCT` (e.g. 45), or lower `INSTANT_ALERTS_PER_HOUR`.
- **Too few:** lower `PRICE_ANOMALY_MIN_DROP_PCT` (e.g. 25) and wait for history to build (a few cycles).
- **A destination you never want:** `/mute XXX` or `excluded_destinations`.
- **A destination you always want:** `/priority XXX` or `priority_destinations`.
- If your `.env` came from an older `.env.example`, remove `EUROPE_TRIP_MAX_EUR=120` / `LONGHAUL_TRIP_MAX_EUR=450`: those old defaults made almost every fare instant (`doctor` warns about this).

---

## Project structure

```
main.py                     CLI (run, cycle, digest, doctor, health, healthcheck, status, search)
config.py                   Settings (.env) and constants
preferences.py              Preferences YAML + Telegram overrides
config/user_preferences.yaml(.example)

scheduler/
  planner.py                Search tasks per source: budgets, rotation, priorities, hubs
  runner.py                 The pipeline cycle, digest, scheduler, graceful shutdown
scrapers/
  aggregator.py             Runs sources concurrently, records health
  ryanair.py, google_flights.py, travelpayouts.py, skyscanner.py
  piratinviaggio.py         Deal posts (flights, packages, hotels) read by Claude
  google_hotels.py, booking_com.py, secret_flying.py, going.py, holiday_pirates.py (off by default)
  health_monitor.py         Per-source outcomes, FAILING / recovered notices
normalizers/                EUR conversion, confidence, grouping
trip_builder/               Trip assembly, feasibility, categories, repositioning
filters/                    Alert policy, hard filters, time decay, fuzzy dedup
storage/                    Models, SQLite, price history and baselines
ai_layer/                   Alert formatting (+ optional polish), post extraction
notifier/                   Telegram delivery, rate limiter, commands
utils/                      Airport registry, markets, links, doctor, time
tests/                      ~480 tests; no network, temporary databases
docs/                       Architecture, configuration, deployment, Telegram, roadmap
```

---

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

Tests use temporary databases and mocked HTTP (respx); scrapers are tested on trimmed pages recorded from the live sites. CI runs the same on every push and pull request to `main`.

---

## Source reliability

Ryanair's fare API is the backbone. Google Flights throttles datacenter IPs, so its budget is small and it cools down for a few hours after a block. Booking.com (bot wall), Google Hotels (dates can't be set), Secret Flying (Cloudflare), Going (JavaScript) and the UK HolidayPirates site are off by default; `python main.py health` and `doctor` show why each source is off.

## Docs

- [Architecture](docs/architecture.md)
- [Configuration reference](docs/configuration.md)
- [Deployment (Docker / systemd)](docs/deployment.md)
- [Telegram setup and commands](docs/telegram_setup.md)
- [Roadmap and progress](docs/ROADMAP.md)
