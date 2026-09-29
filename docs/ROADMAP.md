# CheapTrip — Status Report & Branch Roadmap

## Where things stand (end of Round 3)

Every card below (B01–B27) is merged into `main`; the progress tracker at the end has the details. In short:

- **Data:** Ryanair's fare API is the backbone (about 750 fares per cycle), Google Flights adds exact-route searches with a cooldown when Google throttles, and Travelpayouts is ready once a token is set. PiratinViaggio posts (flights, packages, hotels) are read by Claude when an Anthropic key is set.
- **Alerts:** a fare is instant when it is well below the usual price for its route, length and month in earlier cycles (or a priority destination, a possible error fare, or under the €25/€250 backstop). Near-duplicates merge, and a known deal alerts again only after a real price drop.
- **Control:** Telegram commands (`/deals`, `/mute`, `/priority`, `/budget`, `/pause`), any home airport, `main.py doctor` and a Docker healthcheck.
- **Still open:** real hotel prices (flight alerts link a Booking.com search instead), and live runs of the Claude reader and Telegram commands, which need the owner's keys.

The rest of this document is the original review and plan; its "Status at a glance" section describes the code before Round 1.

_Original review: 2026-09-28, at commit `85711de`._

## Context
This is a full review of the codebase (~7.8k lines, 71 files): where things stand, plus a work plan that splits every missing or broken feature into its own branch. We work through them one at a time.
Findings marked **(confirmed)** were traced line by line in the code. File:line references point to commit `85711de`.

---

## 1. Status at a glance

### What's solid
- The pipeline is wired end to end: date windows → scrapers → normalize → build trips → hard filters → time decay → dedup → Telegram. Each scraper's failures are isolated, and the cycle never crashes the scheduler.
- The code is cleanly split into modules with Pydantic models. Pricing and verdicts are deterministic, and the AI is only used for formatting.
- Confidence scoring, airport clusters, flexible date windows, the feasibility engine, the categorizer, the rate limiter (restored from the DB) and SQLite storage all work.
- CLI commands: `run`, `cycle`, `digest`, `status`, `health`, `search`, `resolve-airports`. There's also Docker, docs, and 140 unit tests, all on pure logic.

### What's broken or only exists on paper
| Area | Reality |
|---|---|
| **Real data** | Almost none reaches the pipeline. Skyscanner (RapidAPI) makes about 540 calls per cycle, which guarantees 429 rate-limit errors. The Google Flights regex scraper most likely returns nothing. Booking.com usually blocks plain httpx. Secret Flying and Going are disabled, and their enable flags don't even load from `.env`. HolidayPirates guesses airports from any 3-letter word. |
| **Coverage** | Every search only uses the first 10 (Skyscanner), 8 (Google) or 5 (Booking) destinations, so all ~30 long-haul destinations are never searched. |
| **Daily digest** | Crashes for every deal loaded from the DB, so it has never sent. |
| **Health monitor** | Always shows green, and `main.py health` always prints "no data". |
| **Features on paper only** | Priority destinations, anomaly→INSTANT upgrade, booking-confidence gate for instant alerts, repositioning, hotel-only deals, fuzzy dedup, the `/deals` command, Friday departures for weekends, price trend, `min_hotel_review_count`, `allow_repositioning`, `hotel_low_rating_discount_threshold`, retrying queued instant alerts. |

---

## 2. Branch workflow
- **Base branch:** `main`, created from `85711de`. Each branch below is cut from `main` and merged back through a PR.
- **One branch = one card below.** Each branch adds tests for what it fixes, and every PR must pass CI (added by B01).
- Sizes: S ≈ under 1 session, M ≈ 1 session, L ≈ 2+ sessions.
- The order matters: Phase 0 unblocks everything. Within a phase, cards can be done in any order unless a card says "depends on".

---

## 3. Roadmap

### Phase 0 — Foundations

**B01 `chore/dev-tooling-ci`** (S)
- Add `pyproject.toml` with pytest config (`asyncio_mode=auto`) and a ruff config. Split dev dependencies into `requirements-dev.txt`.
- Remove unused dependencies: `playwright`, `SQLAlchemy`, `python-telegram-bot`, `forex-python`, `pytest-httpx`. Either call `python-dotenv` (`load_dotenv()`) or drop it. Bump `anthropic` from 0.28.
- Add a GitHub Actions workflow: install, ruff, pytest.
- Done when: CI is green on the current code.

**B02 `test/pipeline-harness`** (M) — depends on B01
- Add an end-to-end test harness: fake flight/hotel scrapers, a fake Telegram transport (captures messages), a temp DB, and frozen time. It runs `run_pipeline_cycle()` and `run_daily_digest()`.
- It should reproduce the digest crash (B04) as an xfail test.
- Done when: later branches can assert on "which messages would be sent".

**B03 `refactor/airport-registry`** (M) — depends on B01
- Create one source of truth, e.g. `utils/airports.py`, with IATA → city, country, region, lat/lon, is_beach and cluster.
- Today this data is duplicated and inconsistent across `config.py` (POPULAR_DESTINATIONS, AIRPORT_CLUSTERS), `normalizers/currency.py:46` (AIRPORT_TO_CITY), `scrapers/skyscanner.py:110` (_IATA_TO_CITY), `trip_builder/builder.py:33`, `filters/hard_filters.py:12`, `trip_builder/feasibility.py:21-78` and `trip_builder/categorizer.py:14-33`.
- Fix **(confirmed)**: the hard-filter "Europe" set is missing STN/LTN/ORY/GRO/AAR/BMA/TRF. Those destinations get the €450 long-haul threshold, so a €300 London flight becomes INSTANT.
- Replace the MXP-only flight-hours table with a great-circle estimate. Today LIN/BGY origins fall back to crude regional guesses.

### Phase 1 — Critical bugs (small and independent)

**B04 `fix/digest-crash`** (S) — depends on B02
- **(confirmed)** `Trip` uses `use_enum_values=True` (`storage/models.py:187`). Trips loaded from the DB therefore hold plain strings, and `trip.category.value` / `trip.booking_confidence.value` raise AttributeError (`ai_layer/formatter.py:20,197-201`). The digest has never sent.
- The digest rate-limit slot is also used up before the crash (`notifier/telegram.py:111`).
- The digest cron runs in UTC while the docs say Europe/Rome (`scheduler/runner.py:228`). Add a `TIMEZONE` setting.
- Group the digest by route (best deal per route) so it isn't 15 Krakow variants.
- The cycle-summary icon is always 🏨 (`runner.py:168`: `"FLIGHT" in "flight_only"` is false because of case).

**B05 `fix/alert-tier-logic`** (M)
- **(confirmed)** `apply_hard_filters` recomputes the tier from price only (`filters/hard_filters.py:78-91`). That throws away `priority_destinations` (`builder.py:316`) and the anomaly upgrades (`builder.py:55-63`).
- Fix: move tier assignment into one function used everywhere.
- Enforce `booking_confidence_min_for_instant`, which is currently unused.
- Use `prefs.hotel_low_rating_discount_threshold` instead of the hardcoded 70.
- Remove the duplicate hotel-quality settings in `Settings` vs `UserPreferences`.
- **(confirmed, found in B03)** `get_price_stats` crashes with `round(None)` when a route has exactly one price in the last 30 days (`storage/price_analytics.py:100`), so the anomaly check throws on every new route.

**B06 `fix/telegram-delivery`** (M)
- Switch from legacy Markdown to `parse_mode=HTML` and escape text. Hotel or airline names containing `_ * [` currently cause 400 errors, and those messages are silently dropped.
- `_send_raw` raises `RetryError` after its retries (`utils/retry.py:39` uses `reraise=False` and never returns None). That aborts the rest of the alert queue, and at startup it can crash `run_forever`. Fix: return False and log.
- Release the rate-limit slot when a send fails (`telegram.py:92`).
- Retry queued instant alerts: rate-limited deals are saved with `is_alerted=0` but `get_pending_instant_alerts` is never called (`runner.py:21`), so those deals are lost. Resend them next cycle, subject to time decay.
- Use `AsyncAnthropic`. The sync client in `formatter.py:238` blocks the event loop.
- Make the AI safety check cover dates and IATA codes too, not just € amounts.

**B07 `fix/skyscanner-demote`** (S)
Decision: we are not investing further in Skyscanner. This branch only stops it from producing wrong data or burning quota.
- **(confirmed)** Wrong hardcoded entity IDs: JNB/CPT/NBO all use BKK's ID and TLV uses TPE's (`scrapers/skyscanner.py:76,93-95`). They return Bangkok/Taipei flights labeled as Africa/Israel. Remove them.
- The probe uses a hardcoded date that is now in the past, `date(2026,7,1)` (`:435`). Use today+30.
- Make it opt-in with `ENABLE_SKYSCANNER` in Settings, set to off by default.
- On HTTP 429, disable the source until the next day instead of retrying.
- Cap its calls with a small per-cycle limit so an existing key can't burn about 540 calls per cycle.

**B08 `fix/feed-scrapers`** (M) — depends on B03
- **(confirmed)** Secret Flying, HolidayPirates and Going are date-independent feeds, but they run once per date batch (~18× per cycle). Each run stamps the batch's `departure_date_from` on the deal, which fabricates the date and creates 18 "different" trips (`holiday_pirates.py:218`, `secret_flying.py:123`, `going.py:101`).
- Fix: run feed scrapers once per cycle and make `departure_date` optional or flagged as estimated.
- The airport regex takes any 3-letter word (THE, AND, EUR). Validate matches against the airport registry.
- `ENABLE_SECRET_FLYING` and `ENABLE_GOING` are read with `os.getenv` (`secret_flying.py:62`, `going.py:36`), so they're ignored when set in `.env` locally. Move them into `Settings`.
- Flag posts from the Secret Flying `error-fares` category as error fares.

**B09 `fix/health-monitor`** (M)
- **(confirmed)** `safe_scrape` swallows every exception (`scrapers/base.py:56-65`), so the aggregator always records success (`aggregator.py:75`). Disabled scrapers also count as healthy.
- `main.py health` runs in a new process with an empty in-memory registry, so it always prints "no data".
- Fix: return a `ScrapeOutcome` (ok/empty/error/disabled/rate_limited) and persist it to a `scraper_health` table. The CLI reads that table, and a one-time Telegram alert fires when a source becomes FAILING.

### Phase 2 — Data coverage & sources (the real blocker)

**Chosen sources:** Ryanair/Wizz (B11), Travelpayouts (B12) and the Google Flights rewrite (B13). Skyscanner is demoted in B07.

**B10 `feat/search-planner`** (L) — depends on B03
- **(confirmed)** Skyscanner searches `origins[:3] × destinations[:10]` for every one of ~18 batches (`skyscanner.py:631`). Google uses `[:8]` and Booking `[:5]`. The same ~10 EU cities are searched every time, and there are about 540 Skyscanner calls per cycle.
- Build a planner:
  - A per-source daily request budget, set in Settings.
  - Round-robin rotation over (origin cluster, destination, date) with a cursor persisted in the DB, so coverage spreads across days.
  - More frequent checks for priority destinations.
  - Friday departures for the weekend profile (promised in `date_discovery.py:37`, not implemented).
- Fix the return date: it is currently `dep + nights_min + 1` while hotels use `nights_min` nights.

**B11 `feat/source-ryanair-wizz`** (M) — depends on B10
- Add new `scrapers/ryanair.py` and `scrapers/wizzair.py` using the public fare-finder / cheapest-per-day endpoints. They're unofficial but free.
- One call covers a whole month of departure dates, which fits the flexible-date model directly.
- Map the results to `RawFlightResult` with real dates, times and one-way/round-trip.
- Handle their rate limits politely and add recorded-JSON parsing tests.

**B12 `feat/source-travelpayouts`** (M) — depends on B10
- Add new `scrapers/travelpayouts.py` using the Aviasales Data API with a free `TRAVELPAYOUTS_TOKEN` in Settings.
- Use it for cheapest prices per month and destination, plus "anywhere" discovery.
- These are cached prices, so give them a lower reliability weight and mark them "verify before booking".
- Add fixture tests.

**B13 `fix/google-flights`** (M)
- The current scraper regexes `"price":` out of the HTML and pairs prices with airlines by position (`google_flights.py:95-114`). In the EU it probably hits the consent wall. It is also weighted 0.90 for reliability.
- Rewrite it with the protobuf `tfs=` URL approach (as the `fast-flights` library does).
- Pass the EU consent handling.
- Parse actual itineraries: price, airline, times and stops.
- Fall back to disabled, with a health status, when it's blocked.

**B14 `fix/hotel-sources`** (M–L) — decision
- Booking.com HTML is usually blocked.
- Price parsing breaks on thousands separators: "1,234" becomes 1.23 (`booking_com.py:95`).
- `nflt` is double URL-encoded (`:60`).
- The HolidayPirates hotel parser treats a package price as a per-night price.
- Options: the RapidAPI Booking API (same key), the Hotellook/Travelpayouts hotel API, or Playwright.

### Phase 3 — Deal logic quality

- **Outcome (Round 1):**
  - Google Hotels cards parse cleanly (name, nightly and total price, rating, reviews, class, dates), but **dates can't be set**. `checkin`/`checkout` URL parameters and dates in the query text are ignored.
  - The `ts=` encoding couldn't be captured: headless Chromium didn't respond to date-picker clicks.
  - The scraper ships **disabled**. It only keeps cards whose own dates match the requested stay, and otherwise reports BLOCKED.
  - Booking.com HTML is blocked by AWS WAF and is also off by default.
  - A working hotel source is a **re-plan decision**: RapidAPI Booking, Playwright-driven Booking, or a hotel data API.

**B15 `fix/trip-assembly`** (L) — depends on B10
- **(confirmed)** The builder filters origins against the hardcoded LAYER_1/2/3 lists (`builder.py:127,147`). If `home_airports` is changed to e.g. TRN, every trip is dropped. Use preferences instead.
- Flight-only trips have `nights=None`, so they default to the SHORT profile with a 12h cap. Long-haul flights over 12h are then marked infeasible and downgraded. Also propagate the profile from the search params.
- Complete trips pair any hotel with any flight regardless of dates (`builder.py:162`). `normalize_hotels` also groups by name+location only (`normalizers/hotel.py:24`), which collapses different dates together.
- Fix: match on check-in == departure date and nights == return − departure.
- Model one-way vs round-trip explicitly. `return_flight` is never populated.
- Remove the dead quality check at `builder.py:238`.

**B16 `fix/price-history-anomaly`** (M–L)
- **(confirmed)** Prices are recorded *before* anomaly detection (`builder.py:118-123`). The "sudden drop" check therefore compares against a price from the same cycle, which makes it effectively dead. "All-time low" also includes the current price.
- The route key ignores trip type, nights and departure month.
- Fix: key on `(origin cluster, dest, one-way/RT, nights bucket, month)` and exclude the current cycle from the stats.
- Add DB retention: `expires_at` is never used and `price_history` grows without limit.
- Batch the DB writes: today each insert opens a new connection.
- Implement "price trend" (a 7-day slope), which a commit message claims exists but doesn't.
- Treat unknown currencies as errors instead of silently as EUR (`currency.py:156`).

**B17 `feat/fuzzy-dedup`** (M)
- The hash includes the exact price (`models.py:155-162`), so a €1 change becomes a "new deal".
- `DEDUP_PRICE_TOLERANCE_PCT`, `DEDUP_DATE_TOLERANCE_DAYS` and `DEDUP_WINDOW_DAYS` are imported but never used.
- Implement ±2 days / ±5% / 7-day matching. Re-alert only on a real further price drop.

**B18 `fix/repositioning`** (M) — depends on B10, B15
- This is effectively dead:
  - Hub departures (e.g. LHR→X) are never searched, because origins are only the home cluster.
  - There's no date alignment between the legs.
  - The `allow_repositioning` preference is ignored.
  - The layover check `h + 2 > 20` never fires (`repositioning.py:126`).
  - There's no return journey.
- Fix: have the planner add hub→long-haul searches when enabled, add real layover/date rules, and honor the preference.

**B19 `feat/hotel-only-deals`** (S–M) — depends on B14
- `DealType.HOTEL_ONLY`, its formatter, its filter rule and the "Hotel Steal" category all exist, but the builder never creates hotel-only trips. Implement it or remove it.
- Implement `min_hotel_review_count`: review counts are never scraped or filtered.

### Phase 4 — Features

**B20 `feat/telegram-commands`** (M) — depends on B06
- The digest says "Reply /deals" but no bot command handling exists.
- Add a getUpdates loop in the same asyncio loop, restricted to `TELEGRAM_CHAT_ID`, with these commands:
  - `/deals`, `/status`, `/health`
  - `/mute <IATA>`, `/priority <IATA>`
  - `/pause`, `/resume`, `/budget`
- Store these preference overrides in the DB.

**B21 `feat/ai-feed-extraction`** (M, optional) — depends on B08
- **Found in B08:** holidaypirates.com is the UK edition (GBP package deals from UK airports), so it rarely yields flights for an Italy-based user. The Italian sister site piratinviaggio.it fits better, but its posts are Italian ("Voli da Milano a Cracovia a 19€"), which suits AI extraction rather than regex.
- Use Claude Haiku to extract structured deals (origin, dest, date range, price, currency) from feed posts instead of regex. Cache by URL, and validate IATA codes against the registry.

**B22 `feat/generalize-preferences`** (S–M) — depends on B03
- These are hardcoded to Milan/Italy today:
  - `adults` is always 1.
  - The Skyscanner market is always `IT`.
  - The Secret Flying filter only keeps `_ITALY_AIRPORTS`.
  - `LAYER_*` are fixed lists.
- Drive all of them from preferences so a non-Milan user works.

### Phase 5 — Ops & docs

**B23 `chore/ops-hardening`** (S–M)
- Graceful SIGTERM shutdown.
- The Docker healthcheck always passes, because `status` catches everything and exits 0 (`main.py:117`). Make it fail when the last cycle is older than 3× the interval.
- Run the container as a non-root user.
- Use timezone-aware datetimes instead of the deprecated `utcnow()`.
- Validate config at startup (e.g. a `main.py doctor` command that warns when Telegram or keys are missing).

**B24 `docs/sync`** (S) — do last, or bit by bit per branch
- `config/user_preferences.yaml.example` doesn't exist.
- The clone URL is wrong (`cheaptrip-by-claude`).
- The docs say `SCRAPE_INTERVAL` defaults to 30; it's 90.
- The digest timezone claim is wrong.
- "Instant requires HIGH/MEDIUM confidence" and "30% below median" are both untrue today.
- Secret Flying and Going are listed as working free sources.
- The "~130 tests" count is out of date.

### Added in Round 2

**B25 `fix/city-names`** (S)
- Some airports resolve to a suburb or municipality instead of the city travellers know: "Jasionka" (RZE, Rzeszów), "Rinas" (TIA, Tirana), "Oulad Yaich" (BEM, Beni Mellal), "Nottingham, Leicestershire" (EMA, East Midlands).
- Diff the registry against the city names Ryanair's fare API returns and curate `_CITY_OVERRIDES`.

**B26 `feat/hotel-search-link`** (S)
- No free hotel source works yet (B14), so flight alerts with a return date get a Booking.com search link pre-filled with the city and the trip dates.

### Added in Round 3

**B27 `fix/first-anomaly-google-calm`** (S)
- Found in the Round 2 dry run: fares saved to the digest on a cold start never alert, even once history shows them to be unusually cheap (7 qualified in cycle 3, 0 sent). Offer such a deal as instant once.
- Google Flights was rate-limited in 2 of 3 cycles: lower budget, wider spacing, and a cooldown after a block.

### Added in Round 4

**B28 `fix/error-fare-rule`** (S)
- Found in the Round 3 dry run: 3 of 6 instant alerts said "possible error fare", including Berlin at €30 against a usual €79. The rule was "under 40% of the usual price" on baselines only hours old.
- Now a fare is a possible error fare only at ≤ 35% of a usual price built from ≥ 20 fares over ≥ 3 days, and ≥ €100 under it (or when the source flags it).

**B29 `fix/google-cooldown-persist`** (S)
- The Google Flights cooldown is kept in memory only, so separate `main.py cycle` runs (cron) forget it. Store it in the database.

**B30 `feat/first-live-run`** (S–M)
- Checks for the first run with real keys: `doctor` verifies the chat, webhook and Anthropic key; `main.py test-alert` sends a test message; `main.py read-feed` shows what the Claude reader extracts without saving anything.

---

## 4. Suggested order
B01 → B02 → B03 → B04 → B05 → B06 → B07 → B08 → B09 → B10 → B11 → B12 → B13 → B14 → B15 → B16 → B17 → B18 → B19 → B20 → B22 → B23 → B21 (optional) → B24.

If you only want the fastest path to "useful alerts on my phone": B01, B02, B03, B04, B05, B06, B07, B10, B11, B15.

## 5. Verification (every branch)
- `pytest` and `ruff` pass in CI (from B01).
- Each branch adds regression tests for its own bug. Pipeline-level bugs are tested through the B02 harness, which asserts on captured Telegram messages.
- Scraper branches: add recorded-fixture parsing tests (saved JSON/HTML), plus one manual `python main.py search --origin MXP --dest KRK` run with real keys, whose output we check together.
- Behavior branches: run a `python main.py cycle` dry run (no Telegram token) and check the logs for `hard_filter_results`, `dedup_results` and `instant_alerts_sent`.

## 6. Progress tracker
| Card | Branch | Status |
|---|---|---|
| B01 | `chore/dev-tooling-ci` | done |
| B02 | `test/pipeline-harness` | done |
| B03 | `refactor/airport-registry` | done |
| B04 | `fix/digest-crash` | done |
| B05 | `fix/alert-tier-logic` | done |
| B06 | `fix/telegram-delivery` | done |
| B07 | `fix/skyscanner-demote` | done |
| B08 | `fix/feed-scrapers` | done |
| B09 | `fix/health-monitor` | done |
| B10 | `feat/search-planner` | done |
| B11 | `feat/source-ryanair` (Wizz dropped) | done |
| B12 | `feat/source-travelpayouts` | done (needs a token to go live) |
| B13 | `fix/google-flights` | done |
| B14 | `fix/hotel-sources` | done (no working hotel source yet — re-plan) |
| B15 | `fix/trip-assembly` | done |
| B16 | `fix/price-history-anomaly` | done (7-day price trend dropped: nothing would use it) |
| B17 | `feat/fuzzy-dedup` | done |
| B18 | `fix/repositioning` | done (useful once a long-haul source such as Travelpayouts is on) |
| B19 | `feat/hotel-only-deals` | done |
| B20 | `feat/telegram-commands` | done |
| B21 | `feat/ai-feed-extraction` | done (live model run needs ANTHROPIC_API_KEY) |
| B22 | `feat/generalize-preferences` | done |
| B23 | `chore/ops-hardening` | done |
| B24 | `docs/sync` | done |
| B25 | `fix/city-names` | done |
| B26 | `feat/hotel-search-link` | done |
| B27 | `fix/first-anomaly-google-calm` | done |
| B28 | `fix/error-fare-rule` | done |
| B29 | `fix/google-cooldown-persist` | planned |
| B30 | `feat/first-live-run` | planned |
