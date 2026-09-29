# Architecture

## One cycle (`scheduler/runner.run_pipeline_cycle`)

```
load Telegram overrides ─► preferences (YAML + /mute, /priority, /budget)
        │
        ▼
retention purge ─► price history > 120 days, deals expired > 7 days, old alert records
        │
        ▼
SearchPlanner.plan ─► tasks per source (scheduler/planner.py)
        │   • each source declares a capability: ANYWHERE (Ryanair, Travelpayouts),
        │     ROUTE_DATE (Google Flights, Skyscanner), CITY_DATES (hotels), FEED (posts)
        │   • a per-source budget (calls_per_cycle) and a rotation cursor stored in the DB,
        │     so every destination and date slot is covered over successive cycles
        │   • priority destinations get up to half of a route source's budget
        │   • long-haul sources give 20% to searches from repositioning hubs
        ▼
ScraperAggregator ─► feeds once, search sources with their tasks, hotels (concurrently)
        │   every outcome (ok / empty / partial / error / rate_limited / blocked / disabled)
        │   is recorded in scraper_health; FAILING and recovered notices go to Telegram once
        ▼
normalize_flights / normalize_hotels ─► EUR, confidence, grouping per itinerary / stay
        │
        ▼
build_trips (trip_builder/builder.py)
        │   flight-only · package · hotel-only · complete (same dates) · repositioned
        │   stay length → trip profile → feasibility (travel hours vs trip length) → category
        │   PriceIndex: baseline per (origin city, destination city, round trip / one way,
        │   length profile), fares departing ±30 days, EARLIER cycles → normal price, discount, anomaly
        │   this cycle's dated search fares are then added to price_history
        ▼
collapse_similar ─► one trip per (cities, kind, length profile) within ±2 days: the cheapest;
        │            identical itineraries from several sources merge their source lists
        ▼
apply_hard_filters ─► discard: zero price, muted destination, over budget, low-quality hotel,
        │              too few hotel reviews; then decide_tier (filters/alert_policy.py)
        ▼
apply_time_decay ─► instant deals older than 6h become digest
        │
        ▼
deduplicate_trips ─► same deal saved in the last 7 days? duplicate, unless ≥5% cheaper
        │             ("price dropped from €X") or instant for the first time
        ▼
pending instant queue ─► sorted by discount, sent up to INSTANT_ALERTS_PER_HOUR;
                          the same route alerts at most every 6h unless the price dropped
```

Every cycle is recorded in the `cycles` table and announced on the event bus (`utils/events.py`): `cycle_started`, `cycle_planned` (the sources that will run), `source_done` per source, and `cycle_finished` with its counts.

## The engine as a service (`scheduler/engine.py`)

`Engine` owns what runs 24/7:
- the scheduler (a search every `SCRAPE_INTERVAL_MINUTES` and the daily digest);
- the notifier;
- the Telegram command poller (`notifier/commands.py`).

It also gives the desktop app its controls:
- `search_now()`: cycles share a lock, so two never run at once;
- `pause()` / `resume()`;
- `status()`: state, next and last search, searches today.

The pause state and the mute, priority and budget overrides live in `control.py`. The Telegram commands and the app call the same functions, and each change is published on the event bus.

`main.py run` (and Docker) is `Engine().run()`, which stops cleanly on SIGTERM or SIGINT; stopping cancels a running search. The last completed cycle time is still recorded for `main.py healthcheck`.

## The local API (`api/`)

The desktop app's screens read JSON from a small aiohttp server (`api/server.py`) running on the engine's event loop. `main.py ui` starts it with the engine and opens the browser.

**Protection:**
- it listens on `127.0.0.1` only;
- every `/api` request needs the launch's random token, as a cookie set when the app opens `/?token=…`, or a Bearer header;
- requests for any other Host are refused, which blocks DNS rebinding;
- there are no CORS headers.

| Route (`/api/v1`) | For |
|---|---|
| `GET /status` | Top bar: state, next and last search, learning progress, attention |
| `GET /deals` | Deals: unusually cheap and best of the rest, with filters and sorting |
| `GET /deals/{id}`, `POST /deals/{id}/hide` | Deal detail, with both charts' data (`storage/price_analytics.fare_comparison`) |
| `GET /destinations?tab=`, `POST`/`DELETE /destinations/{code}/priority\|mute`, `GET /airports?q=` | Destinations; the same overrides as Telegram's `/priority` and `/mute` |
| `GET /activity` | Sources, alerts log, search history |
| `GET`/`PUT /settings` | Settings: `.env` (keys masked) and the preferences YAML (comments kept), applied live (`api/settings_io.py`) |
| `GET /setup/checks`, `POST /setup/test-telegram` | First-run checks (`utils/doctor.py`) |
| `POST /engine/search-now`, `/pause`, `/resume` | Controls |
| `GET /events` | Server-sent events from the event bus |

## Key design decisions

### No AI for prices
All prices, discounts and tiers are computed in Python. Claude is used in two places:
- **Alert polish** (optional, `ai_layer/formatter.py`): the polished text is rejected unless every number, airport code and link survives unchanged.
- **Reading deal posts** (`ai_layer/extractor.py`): structured output (`messages.parse` with a Pydantic schema); every answer is validated against the post (known airports, price written in the text, future dates) and cached per URL, so each post is read once.

### Price history without self-comparison
A fare is judged only against earlier cycles' fares on the same route and trip length that depart within 30 days either side of it. Each fare (route and dates) counts once, at its latest price; a baseline needs at least 6 different fares seen over 2 cycles. Feed posts and undated fares never enter the history.

### Airports
`utils/airports.py` loads ~3,200 airports (OurAirports, public domain) with regions, distances and curated city names; `utils/airport_clusters.py` groups airports of one city (Milan = MXP + LIN + BGY, London = LHR + LGW + STN + LTN + ...). Unknown airports reported by a source are learned at runtime.

### Confidence
Each fare gets a `data_confidence_score` (freshness, number of sources, field completeness, source reliability). The `BookingConfidence` tier (HIGH / MEDIUM / LOW) gates instant alerts.

## Database (SQLite, `storage/database.py`)

| Table | Contents |
|---|---|
| `deals` | Every saved trip: tier, sent flag, JSON payload, dedup key, departure date, hidden flag |
| `alerts_sent` | Delivery history (rate limiting) and the channel each alert went to |
| `cycles` | One row per search: trigger, duration, fares, trips, new deals, sent, status or error |
| `price_history` | Fare observations with their key (cities, trip type, length), dates and cycle |
| `scraper_health` | Last outcome, streaks and notices per source |
| `search_cursor` | Planner rotation positions |
| `feed_extractions` | Cached post extractions per URL |
| `engine_state` | Telegram update offset, last cycle time, pause |
| `pref_overrides` | Preferences changed from Telegram |

`init_db()` creates missing tables and columns on startup, so upgrades need no manual migration.
