"""
Operational checks (B23): `main.py healthcheck` for Docker and `main.py doctor`
for a human setting the engine up.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Tuple

import httpx

from config import get_settings
from utils.timeutil import utcnow

# The engine is unhealthy when its last completed cycle is older than this many intervals
STALE_INTERVALS = 3


async def health_status() -> Tuple[bool, str]:
    """(healthy, explanation) from the last completed cycle recorded by the runner."""
    from notifier.commands import LAST_CYCLE_KEY
    from storage.database import get_state

    try:
        last = await get_state(LAST_CYCLE_KEY)
    except Exception as exc:
        return False, f"database unreadable: {exc}"
    if not last:
        return False, "no completed cycle yet"
    age = utcnow() - datetime.fromisoformat(last)
    limit = timedelta(minutes=get_settings().scrape_interval_minutes * STALE_INTERVALS)
    minutes = int(age.total_seconds() // 60)
    if age > limit:
        return False, f"last cycle {minutes} min ago (limit {int(limit.total_seconds() // 60)} min)"
    return True, f"last cycle {minutes} min ago"


@dataclass
class Check:
    level: str  # "ok" | "warn" | "fail"
    name: str
    detail: str

    def line(self) -> str:
        mark = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}[self.level]
        return f"{mark} {self.name}: {self.detail}"


async def _telegram_checks() -> List[Check]:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return [Check("fail", "Telegram", "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are needed to send alerts")]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe")
    except httpx.HTTPError as exc:
        return [Check("warn", "Telegram", f"could not reach Telegram to check the token: {exc}")]
    if resp.status_code != 200 or not resp.json().get("ok"):
        return [Check("fail", "Telegram", f"the bot token was rejected (HTTP {resp.status_code})")]
    name = resp.json().get("result", {}).get("username", "?")
    return [Check("ok", "Telegram", f"bot @{name}, chat {settings.telegram_chat_id}")]


def _key_checks() -> List[Check]:
    settings = get_settings()
    checks = []
    if settings.anthropic_api_key:
        checks.append(Check("ok", "Anthropic", "key set: alert polish and PiratinViaggio reader available"))
    else:
        checks.append(Check("warn", "Anthropic", "no ANTHROPIC_API_KEY: no alert polish, PiratinViaggio posts not read"))
    if settings.travelpayouts_token:
        checks.append(Check("ok", "Travelpayouts", "token set"))
    else:
        checks.append(Check("warn", "Travelpayouts", "no TRAVELPAYOUTS_TOKEN: one fewer 'search everywhere' source"))
    if settings.europe_trip_max_eur > 50 or settings.longhaul_trip_max_eur > 400:
        checks.append(Check(
            "warn", "Thresholds",
            f"EUROPE_TRIP_MAX_EUR={settings.europe_trip_max_eur:g} / LONGHAUL_TRIP_MAX_EUR="
            f"{settings.longhaul_trip_max_eur:g} look like the old defaults (120/450), which make most fares "
            "instant; the defaults are now 25/250",
        ))
    return checks


def _preference_checks() -> List[Check]:
    import yaml

    from preferences import UserPreferences, preferences_path
    from utils import airports

    path = preferences_path()
    if not path.exists():
        return [Check("warn", "Preferences", f"{path} not found: using defaults (Milan)")]
    try:
        data = yaml.safe_load(path.read_text()) or {}
        known = UserPreferences.model_fields.keys()
        prefs = UserPreferences(**{k: v for k, v in data.items() if k in known and v is not None})
    except Exception as exc:  # the engine itself would silently fall back to defaults
        return [Check("fail", "Preferences", f"{path} can't be used ({type(exc).__name__}: {exc}); "
                                             "the engine would fall back to defaults")]
    checks = [Check("ok", "Preferences", f"{path}: home {', '.join(prefs.home_airports)}")]
    unknown_home = [c for c in prefs.home_airports if not airports.is_known(c)]
    if unknown_home:
        checks.append(Check("fail", "Home airports", f"unknown airport codes: {', '.join(unknown_home)}"))
    lists = prefs.priority_destinations + prefs.excluded_destinations + prefs.repositioning_hubs
    unknown = sorted({c for c in lists if not airports.is_known(c)})
    if unknown:
        checks.append(Check("warn", "Destination lists", f"unknown airport codes: {', '.join(unknown)}"))
    return checks


async def _database_check() -> Check:
    from storage.database import get_db_path, init_db, set_state

    try:
        await init_db()
        await set_state("doctor_checked_at", utcnow().isoformat())
    except Exception as exc:
        return Check("fail", "Database", f"not writable: {exc}")
    return Check("ok", "Database", await get_db_path())


def _source_checks() -> List[Check]:
    from scrapers.aggregator import build_flight_scrapers, build_hotel_scrapers

    checks = []
    for scraper in [*build_flight_scrapers(), *build_hotel_scrapers()]:
        if scraper.enabled:
            checks.append(Check("ok", f"Source {scraper.source_id}", "enabled"))
        else:
            reason = scraper.disabled_reason or "disabled (see its ENABLE_* setting)"
            checks.append(Check("warn", f"Source {scraper.source_id}", reason))
    return checks


async def run_checks() -> List[Check]:
    return [
        *(await _telegram_checks()),
        *_key_checks(),
        *_preference_checks(),
        await _database_check(),
        *_source_checks(),
    ]
