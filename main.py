"""
Travel Deal Intelligence Engine — Main Entry Point

Usage:
  python main.py run           # Start continuous 24/7 engine
  python main.py cycle         # Run a single pipeline cycle and exit
  python main.py digest        # Send today's digest and exit
  python main.py status        # Print DB stats
  python main.py doctor        # Check the configuration (exit 1 on blocking problems)
  python main.py test-alert    # Send a test message with the current top deals to Telegram
  python main.py read-feed     # Show what Claude reads from the newest PiratinViaggio posts
  python main.py healthcheck   # Exit 1 when no cycle completed recently (Docker healthcheck)
"""
from __future__ import annotations

import asyncio
import sys

import click

from utils.logging_config import configure_logging, get_logger

log = get_logger(__name__)


@click.group()
def cli() -> None:
    """Travel Deal Intelligence Engine"""
    configure_logging()


@cli.command()
def run() -> None:
    """Start the continuous 24/7 deal monitoring engine."""
    from scheduler.runner import run_forever
    log.info("engine_starting")
    asyncio.run(run_forever())


@cli.command()
def cycle() -> None:
    """Run a single scraping + alert cycle and exit."""
    from scheduler.runner import init_notifier, run_pipeline_cycle
    from storage.database import init_db

    async def _run():
        await init_db()
        await init_notifier()
        await run_pipeline_cycle()

    asyncio.run(_run())


@cli.command()
def digest() -> None:
    """Send today's deal digest and exit."""
    from scheduler.runner import init_notifier, run_daily_digest
    from storage.database import init_db

    async def _run():
        await init_db()
        await init_notifier()
        await run_daily_digest()

    asyncio.run(_run())


@cli.command()
def status() -> None:
    """Print current engine status and DB statistics."""
    import aiosqlite

    from storage.database import get_db_path

    async def _run():
        path = await get_db_path()
        try:
            async with aiosqlite.connect(path) as db:
                cur = await db.execute("SELECT COUNT(*) FROM deals")
                total = (await cur.fetchone())[0]
                cur = await db.execute("SELECT COUNT(*) FROM deals WHERE is_alerted=1")
                alerted = (await cur.fetchone())[0]
                cur = await db.execute("SELECT COUNT(*) FROM deals WHERE alert_tier='instant'")
                instant = (await cur.fetchone())[0]
                cur = await db.execute("SELECT COUNT(*) FROM price_history")
                prices = (await cur.fetchone())[0]
                cur = await db.execute("SELECT COUNT(*) FROM alerts_sent")
                alerts_sent = (await cur.fetchone())[0]
                cur = await db.execute(
                    "SELECT COUNT(DISTINCT origin_city || dest_city || trip_type || nights_bucket || depart_month)"
                    " FROM price_history WHERE depart_month IS NOT NULL"
                )
                price_keys = (await cur.fetchone())[0]

                cur = await db.execute("""
                    SELECT route, COUNT(*) as n,
                           ROUND(AVG(price_eur), 0) as avg,
                           ROUND(MIN(price_eur), 0) as low
                    FROM price_history
                    GROUP BY route
                    ORDER BY n DESC
                    LIMIT 10
                """)
                top_routes = await cur.fetchall()

            click.echo(f"""
Travel Deal Intelligence Engine — Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Database:         {path}
Total deals:      {total}
Alerted:          {alerted}
Instant-tier:     {instant}
Price records:    {prices}
Price keys:       {price_keys}
Alerts sent:      {alerts_sent}
""")
            if top_routes:
                click.echo("Top tracked routes (by observation count):")
                click.echo(f"  {'Route':<20} {'Count':>6}  {'Avg €':>7}  {'Low €':>7}")
                click.echo("  " + "─" * 46)
                for row in top_routes:
                    click.echo(f"  {row[0]:<20} {row[1]:>6}  {row[2]:>7}  {row[3]:>7}")
            else:
                click.echo("No price history yet (run a cycle first).")
        except Exception as exc:
            click.echo(f"DB not found or not initialized: {exc}")
            click.echo("Run 'python main.py cycle' to initialize.")

    asyncio.run(_run())


@cli.command()
def healthcheck() -> None:
    """Exit 0 when a cycle completed recently, 1 otherwise (used by the Docker healthcheck)."""
    from utils.doctor import health_status

    healthy, detail = asyncio.run(health_status())
    click.echo(("healthy: " if healthy else "unhealthy: ") + detail)
    sys.exit(0 if healthy else 1)


@cli.command()
def doctor() -> None:
    """Check keys, Telegram, preferences, database and sources; exit 1 on blocking problems."""
    from utils.doctor import run_checks

    checks = asyncio.run(run_checks())
    for check in checks:
        click.echo(check.line())
    failures = [c for c in checks if c.level == "fail"]
    click.echo(f"\n{len(failures)} blocking problem(s)." if failures else "\nReady to run.")
    sys.exit(1 if failures else 0)


@cli.command("test-alert")
def test_alert() -> None:
    """Send a test message with the current top deals to your Telegram chat."""
    from utils.doctor import send_test_message

    sent, detail = asyncio.run(send_test_message())
    click.echo(detail)
    sys.exit(0 if sent else 1)


@cli.command("read-feed")
@click.option("--limit", default=5, show_default=True, help="Newest posts to read per reader (flights, hotels)")
def read_feed(limit: int) -> None:
    """Show what Claude extracts from the newest PiratinViaggio posts. Saves nothing."""
    from scrapers.piratinviaggio import PiratinViaggioHotelScraper, PiratinViaggioScraper, describe_offer

    readers = [("Flights and packages", PiratinViaggioScraper()), ("Hotels", PiratinViaggioHotelScraper())]
    if not readers[0][1].enabled:
        click.echo(f"The PiratinViaggio reader is off: {readers[0][1].disabled_reason}.")
        sys.exit(1)

    async def _run() -> bool:
        for title, reader in readers:
            results = await reader.preview(limit)
            if results is None:
                click.echo("Couldn't fetch the PiratinViaggio feed; try again later.")
                return False
            click.echo(f"\n{title} ({len(results)} newest post{'' if len(results) == 1 else 's'})")
            for item, judgement in results:
                click.echo(f"• {item.title}\n  {item.url}")
                if judgement.option:
                    click.echo(f"  ✅ kept: {describe_offer(judgement.option)}")
                else:
                    click.echo(f"  – {judgement.outcome}")
        return True

    sys.exit(0 if asyncio.run(_run()) else 1)


@cli.command("resolve-airports")
def resolve_airports() -> None:
    """Resolve and cache Skyscanner entity IDs for all airports.

    Run this once when your RapidAPI rate limit resets to populate the cache.
    After that, the engine uses cached IDs and doesn't waste API calls.
    """
    from config import POPULAR_DESTINATIONS
    from preferences import get_preferences
    from scrapers.skyscanner import SkyscannerScraper

    async def _run():
        scraper = SkyscannerScraper()
        if not scraper.enabled:
            click.echo("Skyscanner scraper disabled (no RAPIDAPI_KEY). Set it in .env first.")
            return
        prefs = get_preferences()
        all_airports = sorted(set(prefs.home_airports + prefs.repositioning_hubs + POPULAR_DESTINATIONS))
        click.echo(f"Resolving {len(all_airports)} airports...")
        resolved = await scraper.resolve_all_airports(all_airports)
        click.echo(f"\nResolved {len(resolved)}/{len(all_airports)} airports:")
        for iata, eid in sorted(resolved.items()):
            click.echo(f"  {iata}: {eid}")
        if len(resolved) < len(all_airports):
            missing = set(all_airports) - set(resolved.keys())
            click.echo(f"\nMissing ({len(missing)}): {', '.join(sorted(missing))}")
            click.echo("Try again later when rate limit resets.")

    asyncio.run(_run())


@cli.command()
def health() -> None:
    """Show scraper health status (requires at least one cycle to have run)."""
    from scrapers.health_monitor import get_health_monitor
    from storage.database import init_db

    async def _run():
        await init_db()
        monitor = get_health_monitor()
        summary = await monitor.get_summary()
        click.echo(summary)

    asyncio.run(_run())


@cli.command()
@click.option("--origin", default=None, help="IATA origin airport (default: your first home airport)")
@click.option("--dest", default="KRK", help="IATA destination airport")
@click.option("--days", default=14, help="Days from now to depart")
@click.option("--nights", default=3, help="Number of nights")
def search(origin: str, dest: str, days: int, nights: int) -> None:
    """Run a one-off search for a specific route across all enabled sources."""
    from datetime import date, timedelta

    from normalizers.flight import normalize_flights
    from normalizers.hotel import normalize_hotels
    from preferences import get_preferences
    from scheduler.planner import SearchPlanner
    from scrapers.aggregator import ScraperAggregator
    from storage.database import init_db
    from trip_builder.builder import build_trips
    from utils.markets import first_home_airport

    prefs = get_preferences()
    origin = (origin or first_home_airport(prefs)).upper()

    async def _run():
        await init_db()
        aggregator = ScraperAggregator()
        plan = SearchPlanner().plan_route(
            aggregator.sources, origin, dest.upper(), date.today() + timedelta(days=days), nights,
            adults=prefs.adults,
        )
        raw_flights, _ = await aggregator.collect_flights(plan)
        raw_hotels, _ = await aggregator.collect_hotels(plan)
        flights = await normalize_flights(raw_flights)
        hotels = await normalize_hotels(raw_hotels)
        trips = await build_trips(flights, hotels)
        trips.sort(key=lambda t: t.total_cost_eur)
        click.echo(f"\nFound {len(trips)} trips for {origin} → {dest}\n")
        for t in trips[:10]:
            dates = f"{t.departure_date}–{t.return_date}" if t.departure_date else "dates: see deal"
            click.echo(
                f"  {t.route} | €{t.total_cost_eur:.0f} | {dates} | "
                f"{t.deal_type.value} | conf={t.data_confidence_score:.2f} | {t.verdict}"
            )

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
