"""
Benchmark CheapTrip's own work on a month of price history (B46).

Seeds a temporary database the way a month of searches would: 77 priority
deals and ~370k saved prices (about 690 fares per search, every 90 minutes),
then times what grows with history: a search's own processing (sources are
faked, so no network), loading the price history, the Deals and Destinations
screens' requests, and the database's size.

    python scripts/bench.py [--days 30]

Prints a Markdown table, also appended to $GITHUB_STEP_SUMMARY in CI. It only
reports: it never fails a build.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESTINATIONS = """LHR MAN EDI DUB AMS EIN BRU CDG NCE MRS LYS BOD TLS NTE FRA MUC BER HAM DUS CGN STR VIE ZRH GVA BSL
PRG BUD WAW KRK GDN WRO KTW CPH ARN GOT OSL BGO HEL TLL RIX VNO MAD BCN AGP PMI VLC SVQ ALC IBZ LIS OPO FAO
ATH SKG HER RHO CFU MLA LCA SOF OTP BEG ZAG SPU DBV LJU TIA IST AYT TFS LPA FUE ACE BRS BHX GLA NAP PMO CTA
BRI CAG OLB""".split()
SEARCHES_PER_DAY = 16  # every 90 minutes
DATES_PER_SEARCH = 10  # departure dates seen per route and search
SEARCH_FARES = 700     # fares in the timed search


def _peak_memory_mb() -> float:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        kernel32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE  # a 64-bit pseudo-handle, not an int
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return float("nan")
        return counters.PeakWorkingSetSize / 2**20
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 2**10


def _environment(folder: Path) -> None:
    # Windows pipes use the ANSI code page, which has no "→": an unprintable log line would end the search
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{folder / 'bench.db'}"
    os.environ["CHEAPTRIP_PREFS_PATH"] = str(folder / "prefs.yaml")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY", "RAPIDAPI_KEY", "TRAVELPAYOUTS_TOKEN"):
        os.environ[key] = ""
    sys.path.insert(0, str(ROOT))


async def _seed(folder: Path, days: int) -> None:
    """77 priority deals from one search, then `days` of history for their routes."""
    from utils import airports

    codes = [code for code in DESTINATIONS if airports.is_known(code)][:77]
    (folder / "prefs.yaml").write_text(
        "home_airports: [MXP]\nallow_repositioning: false\npriority_destinations: [%s]\n" % ", ".join(codes),
        encoding="utf-8")

    import sqlite3

    from notifier.telegram import TelegramNotifier
    from scheduler.engine import Engine
    from scrapers.aggregator import ScraperAggregator
    from storage.database import get_db_path, init_db
    from tests.harness import FakeFlightScraper, flight

    await init_db()
    fares = [flight(destination=code, price=40.0 + i, days_ahead=20 + i % 30, nights=3) for i, code in enumerate(codes)]
    engine = Engine(await TelegramNotifier.create(), ScraperAggregator(flight_scrapers=[FakeFlightScraper(fares)]),
                    telegram_commands=False)
    await engine.run_cycle()
    await engine.stop()
    try:
        from storage.database import close_connections
    except ImportError:  # before B46
        pass
    else:
        await close_connections()

    path = await get_db_path()
    con = sqlite3.connect(path)
    keys = con.execute("SELECT route, source, origin_city, dest_city, trip_type, nights_bucket, depart_date"
                       " FROM price_history").fetchall()
    rng, now = random.Random(7), datetime.utcnow()

    def rows():
        for k in range(1, days * SEARCHES_PER_DAY + 1):
            at = (now - timedelta(minutes=90 * k)).isoformat()
            for route, source, origin, dest, kind, bucket, depart in keys:
                for _ in range(DATES_PER_SEARCH):
                    d = datetime.fromisoformat(depart) + timedelta(days=rng.randint(-40, 40))
                    yield (route, rng.uniform(30, 120), source, at, origin, dest, kind, bucket, d.strftime("%Y-%m"),
                           f"bench-{k}", d.date().isoformat(), (d + timedelta(days=3)).date().isoformat())

    con.executemany("INSERT INTO price_history (route, price_eur, source, recorded_at, origin_city, dest_city,"
                    " trip_type, nights_bucket, depart_month, cycle_id, depart_date, return_date)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows())
    con.commit()
    con.close()


async def _measure(folder: Path) -> dict:
    import aiosqlite
    from aiohttp.test_utils import TestClient, TestServer

    from api.server import create_app
    from notifier.telegram import TelegramNotifier
    from scheduler.engine import Engine
    from scrapers.aggregator import ScraperAggregator
    from storage.database import get_db_path, init_db
    from storage.price_analytics import PriceIndex
    from tests.harness import FakeFlightScraper, flight

    result: dict = {}
    started = time.perf_counter()
    await init_db()
    result["first start (database checks)"] = time.perf_counter() - started
    path = Path(await get_db_path())
    import sqlite3

    con = sqlite3.connect(path)
    result["prices saved"] = con.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    con.close()

    started = time.perf_counter()
    await PriceIndex.load(exclude_cycle="bench")
    result["price history load"] = time.perf_counter() - started

    codes = (folder / "prefs.yaml").read_text(encoding="utf-8").split("[")[-1].split("]")[0].split(", ")
    rng = random.Random(3)
    fares = [flight(destination=rng.choice(codes), price=rng.uniform(30, 150), days_ahead=rng.randint(5, 90),
                    nights=rng.choice([2, 3, 4, 6, 10])) for _ in range(SEARCH_FARES)]
    engine = Engine(await TelegramNotifier.create(), ScraperAggregator(flight_scrapers=[FakeFlightScraper(fares)]),
                    telegram_commands=False)
    client = TestClient(TestServer(create_app(engine, "bench")))  # the screens are open while it searches
    await client.start_server()
    opened = {"count": 0}
    connect = aiosqlite.connect

    def counting(*args, **kwargs):
        opened["count"] += 1
        return connect(*args, **kwargs)

    aiosqlite.connect = counting
    started = time.perf_counter()
    await engine.run_cycle()
    result["search (700 fares, no network)"] = time.perf_counter() - started
    aiosqlite.connect = connect
    result["database connections opened by the search"] = opened["count"]
    result["peak memory (MB)"] = _peak_memory_mb()

    headers = {"Authorization": "Bearer bench"}
    for label, url in (("Deals screen", "/api/v1/deals"), ("Deals count (sidebar)", "/api/v1/deals?summary=1"),
                       ("Destinations screen, the moment the search ends", "/api/v1/destinations?tab=seen"),
                       ("Destinations screen, after that", "/api/v1/destinations?tab=seen")):
        started = time.perf_counter()
        resp = await client.get(url, headers=headers)
        await resp.read()
        result[label] = time.perf_counter() - started
    await client.close()
    await engine.stop()
    try:
        from storage.database import close_connections
    except ImportError:  # before B46
        pass
    else:
        await close_connections()
    result["database size (MB)"] = sum(p.stat().st_size for p in path.parent.glob(path.name + "*")) / 1e6
    return result


def _run_step(step: str, folder: Path, days: int) -> dict:
    out = subprocess.run([sys.executable, __file__, "--step", step, "--folder", str(folder), "--days", str(days)],
                         capture_output=True, text=True, cwd=folder)
    if out.returncode != 0:
        raise SystemExit(f"{step} failed:\n{out.stdout[-3000:]}\n{out.stderr[-3000:]}")
    line = [line for line in out.stdout.splitlines() if line.startswith("RESULT ")]
    return json.loads(line[-1][len("RESULT "):]) if line else {}


def _table(result: dict, days: int) -> str:
    lines = [f"### CheapTrip benchmark: {days} days of price history ({sys.platform}, Python {sys.version.split()[0]})",
             "", "| Measure | Result |", "|---|---|"]
    for label, value in result.items():
        if isinstance(value, float) and value != value:  # NaN: couldn't be measured here
            text = "n/a"
        elif isinstance(value, float) and "MB" not in label:
            text = f"{value:.2f} s"
        elif isinstance(value, float):
            text = f"{value:.0f} MB"
        else:
            text = f"{value:,}"
        lines.append(f"| {label} | {text} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--step", choices=["seed", "measure"])
    parser.add_argument("--folder")
    args = parser.parse_args()

    if args.step:  # a child process: one step, isolated (its memory is measured alone)
        import asyncio

        folder = Path(args.folder)
        _environment(folder)
        if args.step == "seed":
            asyncio.run(_seed(folder, args.days))
        else:
            print("RESULT " + json.dumps(asyncio.run(_measure(folder))))
        return

    with tempfile.TemporaryDirectory(prefix="cheaptrip-bench-") as tmp:
        folder = Path(tmp)
        started = time.perf_counter()
        _run_step("seed", folder, args.days)
        print(f"seeded in {time.perf_counter() - started:.0f} s", file=sys.stderr)
        table = _table(_run_step("measure", folder, args.days), args.days)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(table + "\n")


if __name__ == "__main__":
    main()
