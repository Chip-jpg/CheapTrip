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
        │   length profile, month) from EARLIER cycles → normal price, discount, anomaly
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

`run_forever` also starts the Telegram command poller (`notifier/commands.py`), schedules the daily digest, records the last completed cycle (used by `main.py healthcheck`) and stops cleanly on SIGTERM.

## Key design decisions

### No AI for prices
All prices, discounts and tiers are computed in Python. Claude is used in two places:
- **Alert polish** (optional, `ai_layer/formatter.py`): the polished text is rejected unless every number, airport code and link survives unchanged.
- **Reading deal posts** (`ai_layer/extractor.py`): structured output (`messages.parse` with a Pydantic schema); every answer is validated against the post (known airports, price written in the text, future dates) and cached per URL, so each post is read once.

### Price history without self-comparison
A fare is judged only against earlier cycles of the same route, trip length and month. A key needs at least 6 observations from 2 cycles before it has a baseline. Feed posts and undated fares never enter the history.

### Airports
`utils/airports.py` loads ~3,200 airports (OurAirports, public domain) with regions, distances and curated city names; `utils/airport_clusters.py` groups airports of one city (Milan = MXP + LIN + BGY, London = LHR + LGW + STN + LTN + ...). Unknown airports reported by a source are learned at runtime.

### Confidence
Each fare gets a `data_confidence_score` (freshness, number of sources, field completeness, source reliability). The `BookingConfidence` tier (HIGH / MEDIUM / LOW) gates instant alerts.

## Database (SQLite, `storage/database.py`)

| Table | Contents |
|---|---|
| `deals` | Every saved trip: tier, sent flag, JSON payload, dedup key and departure date |
| `alerts_sent` | Delivery history (rate limiting) |
| `price_history` | Fare observations with their key (cities, trip type, length, month) and cycle |
| `scraper_health` | Last outcome, streaks and notices per source |
| `search_cursor` | Planner rotation positions |
| `feed_extractions` | Cached post extractions per URL |
| `engine_state` | Telegram update offset, last cycle time, pause |
| `pref_overrides` | Preferences changed from Telegram |

`init_db()` creates missing tables and columns on startup, so upgrades need no manual migration.
