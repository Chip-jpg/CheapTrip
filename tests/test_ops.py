"""Ops hardening (B23): utcnow, healthcheck, doctor, graceful shutdown."""
from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime, timedelta, timezone

import httpx
from click.testing import CliRunner

from main import cli
from notifier.commands import LAST_CYCLE_KEY
from storage.database import init_db, set_state
from tests.harness import TEST_BOT_TOKEN
from utils.doctor import health_status, run_checks
from utils.timeutil import utcnow


def test_utcnow_is_naive_utc():
    now = utcnow()
    assert now.tzinfo is None
    assert abs(now - datetime.now(timezone.utc).replace(tzinfo=None)) < timedelta(seconds=2)


# ── healthcheck ───────────────────────────────────────────────────────────────

async def test_health_follows_the_last_completed_cycle(engine):
    await init_db()
    assert await health_status() == (False, "no completed cycle yet")

    await set_state(LAST_CYCLE_KEY, (utcnow() - timedelta(minutes=20)).isoformat())
    healthy, detail = await health_status()
    assert healthy and detail == "last cycle 20 min ago"

    # Default interval 90 min → unhealthy after 270 min
    await set_state(LAST_CYCLE_KEY, (utcnow() - timedelta(hours=5)).isoformat())
    healthy, detail = await health_status()
    assert not healthy and "limit 270 min" in detail


def test_healthcheck_command_exit_codes(engine):
    runner = CliRunner()
    asyncio.run(init_db())
    unhealthy = runner.invoke(cli, ["healthcheck"])
    assert unhealthy.exit_code == 1 and "unhealthy" in unhealthy.output

    asyncio.run(set_state(LAST_CYCLE_KEY, utcnow().isoformat()))
    healthy = runner.invoke(cli, ["healthcheck"])
    assert healthy.exit_code == 0 and "healthy: last cycle 0 min ago" in healthy.output


# ── doctor ────────────────────────────────────────────────────────────────────

def _lines(checks):
    return {c.name: (c.level, c.detail) for c in checks}


async def test_doctor_reports_setup(engine, monkeypatch):
    engine.router.get(f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "CheapTripBot"}}))

    checks = _lines(await run_checks())

    assert checks["Telegram"] == ("ok", "bot @CheapTripBot, chat 4242")
    assert checks["Anthropic"][0] == "warn" and checks["Travelpayouts"][0] == "warn"
    assert checks["Preferences"][0] == "ok" and checks["Database"][0] == "ok"
    assert checks["Source ryanair"] == ("ok", "enabled")
    assert checks["Source travelpayouts"] == ("warn", "needs TRAVELPAYOUTS_TOKEN")
    assert checks["Source secret_flying"][1].startswith("behind Cloudflare")
    assert "Thresholds" not in checks


async def test_doctor_flags_blocking_problems(engine, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("EUROPE_TRIP_MAX_EUR", "120")
    from config import get_settings
    get_settings.cache_clear()
    engine.set_prefs(home_airports=["MXP", "XQZ"], priority_destinations=["JFK", "QQQ"])

    checks = _lines(await run_checks())

    assert checks["Telegram"][0] == "fail"
    assert checks["Home airports"] == ("fail", "unknown airport codes: XQZ")
    assert checks["Destination lists"] == ("warn", "unknown airport codes: QQQ")
    assert checks["Thresholds"][0] == "warn" and "old defaults" in checks["Thresholds"][1]


def test_doctor_command_exits_1_on_failures(engine, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from config import get_settings
    get_settings.cache_clear()

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "❌ Telegram" in result.output and "1 blocking problem(s)." in result.output


# ── graceful shutdown ─────────────────────────────────────────────────────────

async def test_sigterm_stops_the_engine_cleanly(engine, monkeypatch):
    import scheduler.runner as runner

    async def _no_cycle(*args, **kwargs):
        return None

    monkeypatch.setattr(runner, "run_pipeline_cycle", _no_cycle)
    task = asyncio.create_task(runner.run_forever())
    await asyncio.sleep(0.3)  # started: scheduler running, Telegram polling

    os.kill(os.getpid(), signal.SIGTERM)

    await asyncio.wait_for(task, timeout=10)
    assert any("Engine started" in t for t in engine.telegram.texts)
