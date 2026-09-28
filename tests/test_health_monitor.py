"""Scraper health: outcome classification, DB persistence, CLI, failing/recovered notices."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scrapers.base import BaseFlightScraper, ScrapeOutcome, ScrapeStatus
from scrapers.health_monitor import ScraperHealthMonitor
from storage.database import init_db
from tests.harness import flight

REPO_ROOT = Path(__file__).resolve().parent.parent


class _Scraper(BaseFlightScraper):
    source_id = "probe"

    def __init__(self, results=(), errors=(), raise_exc=None, status=None, enabled=True):
        self.enabled = enabled
        self._results, self._errors_to_record, self._raise, self._status = results, errors, raise_exc, status

    async def scrape(self, params):
        for e in self._errors_to_record:
            self._record_error(e)
        if self._status:
            self._report_status(self._status, "told to back off")
        if self._raise:
            raise self._raise
        return list(self._results)


@pytest.mark.parametrize("kwargs,status", [
    (dict(results=[1]), ScrapeStatus.OK),
    (dict(), ScrapeStatus.EMPTY),
    (dict(results=[1], errors=["one pair failed"]), ScrapeStatus.PARTIAL),
    (dict(errors=["every pair failed"]), ScrapeStatus.ERROR),
    (dict(raise_exc=RuntimeError("boom")), ScrapeStatus.ERROR),
    (dict(status=ScrapeStatus.RATE_LIMITED), ScrapeStatus.RATE_LIMITED),
    (dict(enabled=False), ScrapeStatus.DISABLED),
])
async def test_safe_scrape_outcomes(kwargs, status):
    outcome = await _Scraper(**kwargs).safe_scrape(None)
    assert outcome.status == status


async def test_errors_do_not_leak_between_runs():
    scraper = _Scraper(errors=["first run failed"])
    assert (await scraper.safe_scrape(None)).status == ScrapeStatus.ERROR
    scraper._errors_to_record = ()
    assert (await scraper.safe_scrape(None)).status == ScrapeStatus.EMPTY


async def _record(monitor, status, count=0, source="src", error=None):
    results = [object()] * count
    return await monitor.record(ScrapeOutcome(source, status, results, error=error))


async def test_status_transitions(engine):
    await init_db()
    monitor = ScraperHealthMonitor()
    assert (await _record(monitor, ScrapeStatus.OK, 3)).status == "OK"
    assert (await _record(monitor, ScrapeStatus.ERROR, error="HTTP 403")).status == "DEGRADED"
    await _record(monitor, ScrapeStatus.BLOCKED)
    h = await _record(monitor, ScrapeStatus.RATE_LIMITED)
    assert h.status == "FAILING"
    assert h.consecutive_failures == 3
    assert (await _record(monitor, ScrapeStatus.PARTIAL, 2)).status == "OK"


async def test_long_empty_streak_is_reported(engine):
    await init_db()
    monitor = ScraperHealthMonitor()
    for _ in range(10):
        h = await _record(monitor, ScrapeStatus.EMPTY)
    assert h.status == "EMPTY"


async def test_disabled_sources_are_reported_separately(engine):
    await init_db()
    monitor = ScraperHealthMonitor()
    await _record(monitor, ScrapeStatus.DISABLED, source="going")
    await _record(monitor, ScrapeStatus.OK, 5, source="ryanair")
    summary = await monitor.get_summary()
    assert "DISABLED" in summary
    assert "1/1 active sources healthy (1 disabled)" in summary


async def test_health_cli_reads_the_database_from_another_process(engine, tmp_path):
    await init_db()
    monitor = ScraperHealthMonitor()
    await _record(monitor, ScrapeStatus.ERROR, source="google_flights", error="HTTP 429")

    env = dict(os.environ, LOG_FILE=str(tmp_path / "engine.log"), PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run(
        [sys.executable, "main.py", "health"], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "google_flights" in proc.stdout
    assert "DEGRADED" in proc.stdout
    assert "HTTP 429" in proc.stdout


# ── Pipeline notices ─────────────────────────────────────────────────────────

def _blocked(params):
    raise RuntimeError("HTTP 403 Forbidden")


async def test_failing_source_is_notified_once_then_recovery(engine):
    for _ in range(4):
        await engine.run_cycle(flights=_blocked)
    failing = [t for t in engine.telegram.texts if "Source failing" in t]
    assert len(failing) == 1
    assert "fake_flights" in failing[0]
    assert "HTTP 403 Forbidden" in failing[0]

    await engine.run_cycle(flights=[flight(price=300.0)])
    recovered = [t for t in engine.telegram.texts if "Source recovered" in t]
    assert len(recovered) == 1
    assert "fake_flights" in recovered[0]
