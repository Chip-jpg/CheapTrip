"""
Demo data: a month of searches from Milan, with no network.

Seeds a folder the way a month of use would leave it, for the README's screenshots
and the UI's end-to-end tests (ui/e2e):

- preferences: home airports MXP and BGY, priority destinations Lisbon, Kraków and New York;
- 30 days of price history for 31 routes, recorded through the app's own price index;
- today's search, run by the real engine on fake sources (Ryanair, Google Flights and
  Google Hotels): a few fares far below their usual price, the rest at about their usual
  price, and a hotel priced for the stay of each of the best fares;
- the first few unusually cheap deals marked as sent, as the hourly limit would leave them.

    python scripts/demo_data.py --folder <dir>

Then run the app on it (SEARCH_ON_START=false keeps it from searching the real sources):

    CHEAPTRIP_HOME=<dir> DATABASE_URL=sqlite+aiosqlite:///<dir>/demo.db \\
    CHEAPTRIP_PREFS_PATH=<dir>/prefs.yaml SETUP_DONE=true SEARCH_ON_START=false python main.py app
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME_AIRPORTS = ["MXP", "BGY"]
PRIORITY = ["LIS", "KRK", "JFK"]
HISTORY_DAYS = 30
SEARCHES_PER_DAY = 4
DATES_PER_SEARCH = 6   # departure dates seen per route and search
SENT = 5               # unusually cheap deals already sent (the hourly limit holds the rest)

# origin, destination, usual price (€, round trip), nights, airline, today's price as a share of usual
ROUTES = [
    ("MXP", "LIS", 140, 4, "TAP Air Portugal", 0.55),
    ("MXP", "KRK", 95, 3, "Ryanair", 0.97),
    ("MXP", "JFK", 480, 7, "ITA Airways", 0.34),
    ("BGY", "ATH", 130, 5, "Ryanair", 0.48),
    ("MXP", "OPO", 120, 4, "easyJet", 0.50),
    ("BGY", "BUD", 85, 3, "Ryanair", 0.52),
    ("MXP", "VLC", 90, 3, "easyJet", 0.55),
    ("MXP", "DUB", 110, 4, "Aer Lingus", 0.50),
    ("BGY", "CFU", 120, 6, "Ryanair", 0.55),
    ("MXP", "BCN", 95, 3, "easyJet", 1.02),
    ("MXP", "MAD", 105, 4, "Iberia", 0.95),
    ("BGY", "SVQ", 110, 4, "Ryanair", 1.05),
    ("MXP", "AGP", 115, 5, "easyJet", 0.98),
    ("BGY", "PMI", 100, 5, "Ryanair", 1.10),
    ("MXP", "PRG", 90, 3, "easyJet", 0.93),
    ("MXP", "VIE", 85, 3, "Austrian", 1.04),
    ("BGY", "WAW", 75, 3, "Ryanair", 0.96),
    ("MXP", "EDI", 120, 4, "easyJet", 1.08),
    ("BGY", "STN", 70, 3, "Ryanair", 0.99),
    ("MXP", "AMS", 110, 3, "KLM", 0.94),
    ("MXP", "CPH", 130, 4, "SAS", 1.01),
    ("BGY", "HER", 150, 6, "Ryanair", 0.97),
    ("MXP", "BER", 95, 3, "easyJet", 1.06),
    ("MXP", "TFS", 180, 7, "easyJet", 0.92),
    ("BGY", "MLA", 90, 4, "Ryanair", 1.03),
    ("MXP", "OSL", 140, 4, "Norwegian", 0.99),
    ("BGY", "TIA", 70, 3, "Wizz Air", 1.07),
    ("MXP", "NAP", 70, 3, "easyJet", 0.95),
    ("BGY", "PMO", 80, 4, "Ryanair", 1.02),
    ("MXP", "ATH", 140, 5, "Aegean", 1.00),
    ("BGY", "OPO", 115, 4, "Ryanair", 0.97),
]
HOTELS = ["Hotel Centrale", "Old Town Suites", "Riverside Rooms", "Plaza Boutique Hotel", "Harbour View Inn"]


def _environment(folder: Path) -> None:
    # Windows pipes use the ANSI code page, which has no "→": an unprintable log line would end the search
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.environ["CHEAPTRIP_HOME"] = str(folder)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{folder / 'demo.db'}"
    os.environ["CHEAPTRIP_PREFS_PATH"] = str(folder / "prefs.yaml")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY", "TRAVELPAYOUTS_TOKEN"):
        os.environ[key] = ""
    sys.path.insert(0, str(ROOT))


def _source(airline: str) -> str:
    return "ryanair" if airline == "Ryanair" else "google_flights"


async def _seed(folder: Path) -> None:
    (folder / "prefs.yaml").write_text(
        f"home_airports: [{', '.join(HOME_AIRPORTS)}]\nallow_repositioning: false\n"
        f"priority_destinations: [{', '.join(PRIORITY)}]\n", encoding="utf-8")

    from normalizers.flight import normalize_flights
    from notifier.telegram import TelegramNotifier
    from scheduler.engine import Engine
    from scrapers.aggregator import ScraperAggregator
    from scrapers.base import SearchCapability
    from storage.database import close_connections, get_db_path, get_pending_instant_alerts, init_db, mark_alerted
    from storage.models import AlertTier
    from storage.price_analytics import record_fares
    from tests.harness import FakeFlightScraper, FakeHotelScraper, flight, hotel
    from utils import airports
    from utils.timeutil import utcnow

    await init_db()
    rng = random.Random(11)
    today = [(o, d, usual, nights, airline, share, 18 + rng.randint(0, 50)) for o, d, usual, nights, airline, share in ROUTES]

    # A month of searches: each saw a few departures around today's, at about the usual price,
    # and every third one today's exact itinerary too (the deal's "This fare over time")
    searches = HISTORY_DAYS * SEARCHES_PER_DAY
    for k in range(searches, 0, -1):
        raw = []
        for origin, dest, usual, nights, airline, _, ahead in today:
            days = [ahead + rng.randint(-28, 28) for _ in range(DATES_PER_SEARCH)] + ([ahead] if k % 3 == 0 else [])
            raw += [flight(origin=origin, destination=dest, price=round(usual * rng.uniform(0.85, 1.25)),
                           days_ahead=max(day, 3), nights=nights, airline=airline, source=_source(airline))
                    for day in days]
        await record_fares(await normalize_flights(raw), f"demo-{k}")
    await close_connections()
    now = utcnow()
    con = sqlite3.connect(await get_db_path())
    con.executemany("UPDATE price_history SET recorded_at = ? WHERE cycle_id = ?",
                    [((now - timedelta(hours=24 / SEARCHES_PER_DAY * k)).isoformat(), f"demo-{k}")
                     for k in range(1, searches + 1)])
    con.commit()
    con.close()

    # Today's search, through the real engine
    fares = {"ryanair": [], "google_flights": []}
    for origin, dest, usual, nights, airline, share, ahead in today:
        fares[_source(airline)].append(flight(origin=origin, destination=dest, price=round(usual * share),
                                              days_ahead=ahead, nights=nights, airline=airline,
                                              source=_source(airline)))

    def hotels_for(tasks):
        found = []
        for task in tasks:
            city = airports.city_of(task.destination) if task.destination else ""
            for i, name in enumerate(rng.sample(HOTELS, 3)):
                found.append(hotel(name=name, location=city, price_per_night=round(rng.uniform(48, 95) + 12 * i),
                                   nights=task.nights_min, days_ahead=(task.depart_from - date.today()).days,
                                   rating=round(rng.uniform(8.2, 9.2), 1), source="google_hotels",
                                   review_count=rng.randint(300, 2400)))
        return found

    aggregator = ScraperAggregator(
        flight_scrapers=[FakeFlightScraper(fares["ryanair"], source_id="ryanair"),
                         FakeFlightScraper(fares["google_flights"], source_id="google_flights",
                                           capability=SearchCapability.ROUTE_DATE)],
        hotel_scrapers=[FakeHotelScraper(hotels_for, source_id="google_hotels", budget=6)],  # as GOOGLE_HOTELS_CALLS_PER_CYCLE
    )
    engine = Engine(await TelegramNotifier.create(), aggregator, telegram_commands=False)
    await engine.run_cycle()
    await engine.stop()
    for trip in (await get_pending_instant_alerts(limit=100))[:SENT]:
        await mark_alerted(trip.hash, AlertTier.INSTANT, channel="desktop")
    await close_connections()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--folder", type=Path, required=True)
    folder = parser.parse_args().folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    _environment(folder)
    asyncio.run(_seed(folder))


if __name__ == "__main__":
    main()
