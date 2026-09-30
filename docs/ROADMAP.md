# CheapTrip — Status Report & Branch Roadmap

## Where things stand (release v0.8.2)

Every card below (B01–B46) is merged into `main`, and v0.8.2 is published on GitHub Releases with a Windows installer. It is v0.8.0 plus the fixes in B45 (v0.8.1) and the optimizations in B46. The progress tracker at the end has the details. In short:

- **The app (Windows):** one setup `.exe` from the [releases](https://github.com/Chip-jpg/CheapTrip/releases), with no admin rights needed.
  - It is a desktop app in the owner's design ("Fluent Deal Radar"), in light and dark. Its screens: Deals with a detail panel and charts, Destinations, Activity, Notifications & tray, Settings, and a setup wizard at the first launch.
  - Windows notifications with Book / Details / Mute and a picture of the price; a tray icon showing the engine's state.
  - It starts at sign-in; one instance at a time; `cheaptrip://` links.
  - CI builds the installer, installs it and drives the installed app through its API on every change.
  - Elsewhere: Python (`main.py run`, or `main.py ui` for the screens in a browser) or Docker, as before.
- **Data:** Ryanair's fare API is the backbone (about 700–780 fares per search). Google Flights adds exact-route searches and pauses itself after a block. Travelpayouts is ready once a token is set. PiratinViaggio posts (flights, packages, hotels) are read by Claude when an Anthropic key is set.
- **Alerts:** a fare is instant in any of these cases:
  - it is well below the usual price for its route and trip length, among fares departing within 30 days either side;
  - it's for a priority destination;
  - it's a possible error fare;
  - it's under the €25/€250 price floor.

  Alerts go to the desktop and/or Telegram, with one hourly limit for both.
- **Control:** the app, the tray, and Telegram commands (`/deals`, `/mute`, `/priority`, `/budget`, `/pause`).
- **Still open:**
  - real hotel prices: flight alerts link a Booking.com search instead;
  - a code-signing certificate, so Windows SmartScreen stops warning about the installer;
  - how the window, tray and notifications look on real PCs: CI can't display notifications.

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

### Added in Round 5

**B31 `fix/baseline-distinct-fares`** (M)
- Found in the Round 4 dry run: baselines counted every observation, so a fare seen in three cycles counted three times, and dates that happened to be searched again dominated the usual price. Lanzarote's 4 fares made a "usual €205" and "€59 is 71% below usual"; 24 of 27 baselines rested on 5 or fewer different fares.
- Price history now stores each fare's dates; a baseline counts each fare once, at its latest price, and needs 6 different fares.

**B32 `fix/promoted-deal-order`** (S)
- A digest deal promoted to instant kept an empty discount in the database, so the alert queue could rank it below worse deals.

**B33 `fix/holiday-pirates-hotel-id`** (S)
- The HolidayPirates hotel scraper shared the flight scraper's source id, merging their health records.

### Added in Round 6

**B34 `feat/baseline-30-day-window`** (M)
- Found in the Round 5 dry run: with each fare counted once, month keys were too thin (6 of 494 route/length/month keys reached 6 fares).
- A fare is now compared with fares on the same route and trip length departing within 30 days either side. In a 10-cycle dry run, 35–47% of trips had a usual price from cycle 3 on (about 3% before), and 7 fares alerted as unusually cheap.

**B35 `feat/windows-support`** (M)
- Runs as an installed Windows app: version 0.6.0 and `--version`, a per-user app home (`%APPDATA%\CheapTrip`), UTF-8 file handling, a Windows CI job.

**B36 `feat/windows-installer`** (M–L)
- One setup `.exe` (PyInstaller + Inno Setup) built and smoke-tested on Windows in CI, published with each `v*` release.

### Added in Round 7: desktop app foundations

The owner is designing the desktop app in Claude Design. This round builds everything that doesn't depend on the visuals; Round 8 then builds the design and releases v0.8.0.

**B37 `feat/engine-service`** (M)
- An `Engine` service:
  - search now (never two cycles at once), pause and resume;
  - a status snapshot;
  - an event bus announcing cycles and per-source progress.
- `control.py` shares mute, priority, budget and pause between Telegram and the app.
- New tables and columns: a `cycles` history, the channel of each sent alert, and hideable deals.
- aiosqlite 0.20 → 0.22.1: stopping mid-search could leave a database thread running, which kept the process from exiting.

**B38 `feat/local-api`** (L)
- A local, token-protected API feeding every screen, with live updates.

**B39 `feat/desktop-notifications`** (M)
- Windows notifications for deals, alongside Telegram:
  - a deal says what, how much, why and when, with **Book**, **Details** and **Mute <city>** buttons;
  - variants for price drops, possible error fares, packages, hotels, trips with a hotel and routes via a hub, plus the daily digest, source problems and a test.
- `notifier/channels.py` sends each alert to the channels that are on. One hourly limit covers both, and `alerts_sent` records where each alert went. If one channel fails, the other still gets the alert.
- Settings: `NOTIFY_DESKTOP`, `NOTIFY_TELEGRAM`, `NOTIFY_INSTANT`, `NOTIFY_DIGEST`, `NOTIFY_SOURCE_PROBLEMS` and `NOTIFY_SOUND`, applied at once. Telegram command replies are sent even when alerts there are off.
- `POST /setup/test-notification`; `main.py ui` shows desktop notifications, whose buttons open the deal in the browser.
- Tests: the engine fixture now restores environment variables that saving settings sets.

**B40 `feat/desktop-shell`** (L)
- `desktop/`: `CheapTrip.exe`.
  - The engine and API run in a background thread.
  - The window is pywebview on Edge WebView2 (the browser without it). Closing it keeps CheapTrip in the tray.
  - The tray icon shows the state, and its menu has Search now, Pause, Resume and Quit.
  - One engine at a time: a named mutex shared with `run` and `ui`. A second launch or a `cheaptrip://` link hands over to the running app.
- `ui/`: React + Vite + TypeScript screens on the real API with live progress:
  - Deals with filters, and deal detail with both charts;
  - Destinations, Activity, Settings and Setup checks.
  - The typed client `ui/src/api.ts` stays for Round 8.
- Packaging:
  - Two programs: `CheapTrip.exe` (the app) and `cheaptrip-cli.exe` (was `cheaptrip.exe`; Windows file names ignore case).
  - The installer registers `cheaptrip://` and the AppUserModelID, and the sign-in shortcut runs `--minimized`.
  - The smoke test drives the installed app through its API.
  - CI has a `ui` job.
- Fixed on the way:
  - Saving settings rewrote the whole preferences file and dropped a comment. Unchanged values are now left exactly as written.
  - The screens could load before a new install's database tables existed.

### Round 7 status
Everything that doesn't depend on the visuals is on `main`, with no release: the next one is v0.8.0, after Round 8.
- **Engine:** a controllable service.
- **API:** a local, token-protected API for every screen.
- **Notifications:** Windows notifications alongside Telegram.
- **The app:** window, tray, single instance and `cheaptrip://` links.
- **Screens:** plain but working, checked in headless Chromium against live searches.
- **Installer:** smoke-tested in CI.

**Round 8** builds the owner's Claude Design screens on `ui/src/api.ts` and adds the in-app first-run setup, which replaces the installer's wizard pages. It also adds the designed app and tray icons, then light and dark checks against the design, and releases v0.8.0.

### Added in Round 8: the designed app (v0.8.0)
The owner's design ("Fluent Deal Radar", made in Google Stitch) covers Deals with its detail panel, Destinations, the setup wizard, a notifications and tray page, the app icon and the design tokens. Activity and Settings aren't in it, so they are built in the same style. Every element shows real data from the API; the mockup's invented figures (build numbers, latency readouts, photos) are left out.

**B41 `feat/design-system`** (M)
- The design's tokens as Tailwind v4 theme variables: its dark colours, plus a light set derived from its light-mode accents. The fonts (Hanken Grotesk, JetBrains Mono) and icons (Material Symbols) are bundled, so the app works offline.
- **App shell:**
  - top bar: status pill (click for the last and next search, or each source's progress while searching), destination quick jump (Ctrl+K), Search now, Pause alerts / Resume, light and dark switch;
  - sidebar, collapsible to icons: the Deals badge counts unusually cheap deals, and the engine card has "hide to tray".
- **Theme:** system, light or dark (`THEME`); the window opens in the right colour.
- **Identity:** the design's mark rendered into the exe and installer icon, the notification icon and one tray icon per engine state (a status dot on the mark). The tray menu gains a status line and Settings.

**B42 `feat/deals-screen`** (L)
- Summary tiles (unusually cheap, best today, last search, learning prices) and the learning banner, shown while fewer than half the trips have a usual price.
- The filter bar: deal-type chips, trip-length segments, destination text, a max price slider, a departure date window, "Priority only" and the sort order.
- The unusually cheap deals as large cards, one variant per type: flight, error fare, package, hotel, via a hub, flight + hotel. The first six are large; the rest are compact cards. Each card has a fare-range sparkline, why it's here, its sources, whether the alert went out, and Book / Hotels / Details plus a menu: mute, priority, copy link, hide (with Undo).
- "Best of the rest" as a table.
- The deal detail slides over the list: booking confidence in plain words, the reasons, "How this fare compares" (the fares around it with the usual price), "This fare over time", the flights, both legs of a trip via a hub, and the hotel.
- The API adds each unusually cheap deal's fare range to `/deals`; "Best today" is the most unusually cheap deal.

**B43 `feat/destinations-activity`** (L)
- **Destinations:**
  - tiles: priority, muted, lowest fare, last search;
  - Priority / Muted / All seen tabs, with priority destinations as cards (best fare, usual price, 30-day trend);
  - a table of every destination seen, with rule menus and Undo;
  - "Add a destination": autocomplete, then always or never notify;
  - the top bar's quick jump opens a destination here.
- **Activity:** the engine with the learning progress, the hourly limit, every source with its status, note and on/off switch, the notifications sent, the search history, and "Open log folder".
- **Notifications & tray** (the design's "Windows Toasts" page):
  - previews of the toasts CheapTrip would show now, built by the engine from today's deals, and each can be sent to this PC;
  - the tray icon's states and menu;
  - the desktop and sound switches, and a link to Windows' notification settings.
- **Toasts as designed:**
  - buttons read "Book €30", "Grab €150", "View package" and "Reserve";
  - the digest reads "Daily digest: 15 deals from €30";
  - notices get "Open Activity" and use sources' real names;
  - a deal's toast carries a picture of its price and fare range.

**B44 `feat/settings-setup`** (L)
- **Settings:** a section menu, with changes saving as they're made and a confirmation that says when each applies. The sections:
  - Trips: home airports and hubs as chips with autocomplete, travellers, trip lengths, how far ahead, budget, via a hub.
  - Deals & alerts: % below usual, price floors, the hourly limit, the digest time, the search interval, hotel quality.
  - Notifications: channels and kinds, with tests.
  - Accounts & keys: masked keys (never sent back), Telegram steps, and Check buttons for Telegram and Anthropic.
  - Sources.
  - App: start at sign-in, the close button, theme, data folder, the setup wizard, check for updates, and clearing the price history in a danger zone.
- **Setup wizard**, as designed:
  - five steps (welcome, where you fly from, how to tell you, optional extras, checks) with a stepper and a sticky footer;
  - it opens at the first launch until finished (`SETUP_DONE`).
- **Installer:** its question pages go (the wizard asks instead), and so do the Notepad shortcuts. "Start at sign-in" becomes the Run value the app's switch changes; the old Startup shortcut is removed on upgrade.
- **API:** `POST /setup/check`, `GET /app/update` (GitHub's latest release), `POST /data/clear-price-history`, and `setup_needed` in `/status`.

**Release v0.8.0:** version, notes and docs; the merge publishes the installer.

### Round 8 status
The owner's design is built and released as v0.8.0:
- **Design system:** the Stitch tokens in light and dark, bundled fonts and icons, the app shell, and the icons in the exe, installer, tray and notifications.
- **Screens:** Deals (with the detail panel), Destinations, Activity, Notifications & tray, Settings and the setup wizard, all on the live API. Each was checked in headless Chromium in dark and light, with the design's sample data and against live searches.
- **Installer:** the in-app wizard replaces its question pages, and "start at sign-in" is a switch in the app.

### v0.8.1: fixes and tweaks
**B45 `fix/v0.8.1`** (M): five bugs and two tweaks found after v0.8.0, each with a test.
- **Faster Deals screen:** with a month of history (77 deals, about 370k prices) `/deals` took about a minute, and the sidebar's count asked for all of it again.
  - Every card's price comparison ran at once, each on its own database thread.
  - Now they run one at a time, only for the six full cards that show them, on a new index for the "similar fares" lookup: `/deals` takes 0.14 s.
  - The sidebar asks for the summary only (`/deals?summary=1`, 0.02 s).
- **Settings refuse impossible values:** preference ranges (for example 14–365 days ahead, ratings 0–10) with readable errors. A bad value already in the file falls back to its default alone. The fields put the saved value back instead of saving an empty or out-of-range one.
- **Clear filters resets the max price.**
- **Priority and mute exclude each other:** making a destination a priority unmutes it, and muting it ends its priority, from Telegram, the app and notifications alike.
- **The window opens as it was left:** the engine keeps the sidebar (`PUT /ui`), since WebView2 runs in private mode. It marks the saved sidebar and a chosen light or dark theme on the page it serves, so there's no flash of the wrong theme.
- **CI:** the GitHub Actions move to their Node 24 versions.

**Release v0.8.1:** version and notes; the merge publishes the installer.

### v0.8.2: optimizing what we have
**B46 `perf/v0.8.2`** (M): no new features, and the same deals: less work to find them. Measured with `scripts/bench.py` (a month of history: 77 deals, ~370k prices, a search of 700 fares):

| | v0.8.1 | v0.8.2 |
|---|---|---|
| A search's own processing | 9.2 s | 2.9 s |
| Database connections opened by a search | 1,872 | 1 |
| Peak memory | 358 MB | 91 MB |
| Destinations screen | 1.7 s each time | instant (worked out after each search) |
| Database once full | 445 MB (120 days) | 157 MB (45 days) |
| Icon font | 4.0 MB | 99 KB |

- **Price history:** a search loads one summary row per fare (~6k), which the database adds up, instead of every saved price (~370k). A test checks every judgement against the old way.
- **One shared database connection** in WAL mode, instead of one per query. Long reads take their own.
- **Destinations:** its figures come from the database, are cached until the history changes, and are refreshed after each search.
- **Database size:** prices are kept 45 days (was 120), an unused index is gone, and free space is given back (VACUUM) when a quarter of the file is free.
- **Icon font:** trimmed to the 114 icons the app names (`ui/scripts/trim_icons.py`), pixel-identical, outlined and filled.
- **Hidden window:** the screens don't refresh while the window is in the tray; they refresh once when it's shown.
- **`scripts/bench.py`**, which CI's test-windows job runs, with the table in its summary. On Windows: a search takes 2.4 s and peaks at 86 MB.

**Release v0.8.2:** version and notes; the merge publishes the installer.

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
| B29 | `fix/google-cooldown-persist` | done |
| B30 | `feat/first-live-run` | done (run it with your keys: `doctor`, `test-alert`, `read-feed`) |
| B31 | `fix/baseline-distinct-fares` | done |
| B32 | `fix/promoted-deal-order` | done |
| B33 | `fix/holiday-pirates-hotel-id` | done |
| B34 | `feat/baseline-30-day-window` | done |
| B35 | `feat/windows-support` | done |
| B36 | `feat/windows-installer` | done (released as v0.6.0) |
| B37 | `feat/engine-service` | done |
| B38 | `feat/local-api` | done |
| B39 | `feat/desktop-notifications` | done |
| B40 | `feat/desktop-shell` | done |
| B41 | `feat/design-system` | done |
| B42 | `feat/deals-screen` | done |
| B43 | `feat/destinations-activity` | done |
| B44 | `feat/settings-setup` | done |
| — | `release/v0.8.0` | done (released as v0.8.0) |
| B45 | `fix/v0.8.1` | done |
| — | `release/v0.8.1` | done (released as v0.8.1) |
| B46 | `perf/v0.8.2` | done |
| — | `release/v0.8.2` | done (released as v0.8.2) |
