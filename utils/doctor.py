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


async def _telegram_call(client: httpx.AsyncClient, method: str, **params: str) -> Tuple[int, dict]:
    url = f"https://api.telegram.org/bot{get_settings().telegram_bot_token}/{method}"
    resp = await client.get(url, params=params or None)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {}


async def _telegram_checks() -> List[Check]:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return [Check("fail", "Telegram", "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are needed to send alerts")]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            status, me = await _telegram_call(client, "getMe")
            if status != 200 or not me.get("ok"):
                return [Check("fail", "Telegram", f"the bot token was rejected (HTTP {status})")]
            _, chat = await _telegram_call(client, "getChat", chat_id=settings.telegram_chat_id)
            _, hook = await _telegram_call(client, "getWebhookInfo")
    except httpx.HTTPError as exc:
        return [Check("warn", "Telegram", f"could not reach Telegram to check the token: {exc}")]

    name = me.get("result", {}).get("username", "?")
    checks = [Check("ok", "Telegram", f"bot @{name}, chat {settings.telegram_chat_id}")]
    if chat.get("ok"):
        info = chat.get("result", {})
        who = info.get("title") or info.get("username") or info.get("first_name") or "the chat"
        checks.append(Check("ok", "Telegram chat", f"the bot can write to {who}"))
    else:
        checks.append(Check(
            "fail", "Telegram chat",
            f"{chat.get('description') or 'chat not found'}: open the bot in Telegram and press Start "
            "(or add it to your group), and check TELEGRAM_CHAT_ID",
        ))
    if hook.get("ok") and hook.get("result", {}).get("url"):
        checks.append(Check(
            "warn", "Telegram commands",
            "a webhook is set, so commands like /status never reach the engine; remove it by opening "
            "https://api.telegram.org/bot<your token>/deleteWebhook in a browser",
        ))
    else:
        checks.append(Check("ok", "Telegram commands", "no webhook: /commands work while the engine runs"))
    return checks


async def _anthropic_check() -> Check:
    """Looks the configured model up: checks the key and the model name without spending tokens."""
    import anthropic

    from ai_layer.formatter import _make_client

    settings = get_settings()
    if not settings.anthropic_api_key:
        return Check("warn", "Anthropic", "no ANTHROPIC_API_KEY: no alert polish, PiratinViaggio posts not read")
    client = _make_client(settings.anthropic_api_key, 15.0)
    try:
        model = await client.models.retrieve(settings.ai_model)
    except anthropic.AuthenticationError:
        return Check("fail", "Anthropic", "ANTHROPIC_API_KEY was rejected")
    except anthropic.NotFoundError:
        return Check("fail", "Anthropic", f"AI_MODEL {settings.ai_model!r} is not a model this key can use")
    except anthropic.APIStatusError as exc:
        return Check("warn", "Anthropic", f"could not check the key (HTTP {exc.status_code})")
    except anthropic.APIConnectionError as exc:
        return Check("warn", "Anthropic", f"could not reach the Anthropic API to check the key: {exc}")
    finally:
        await client.close()
    return Check("ok", "Anthropic",
                 f"key works with {model.display_name}: alert polish and PiratinViaggio reader available")


def _key_checks() -> List[Check]:
    settings = get_settings()
    checks = []
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
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
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


async def _source_checks() -> List[Check]:
    from scrapers.aggregator import build_flight_scrapers, build_hotel_scrapers

    checks = []
    for scraper in [*build_flight_scrapers(), *build_hotel_scrapers()]:
        await scraper.begin_cycle()  # picks up a stored cooldown (e.g. Google Flights after a block)
        if scraper.enabled:
            checks.append(Check("ok", f"Source {scraper.source_id}", "enabled"))
        else:
            reason = scraper.disabled_reason or "disabled (see its ENABLE_* setting)"
            checks.append(Check("warn", f"Source {scraper.source_id}", reason))
    return checks


async def run_checks() -> List[Check]:
    return [
        *(await _telegram_checks()),
        await _anthropic_check(),
        *_key_checks(),
        *_preference_checks(),
        await _database_check(),
        *(await _source_checks()),
    ]


async def send_test_message() -> Tuple[bool, str]:
    """`main.py test-alert`: send the current top deals to the chat, marked as a test."""
    from notifier.commands import handle_command
    from notifier.telegram import TelegramNotifier
    from storage.database import init_db

    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False, "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are needed: set them in .env, then run `python main.py doctor`."
    await init_db()
    deals = await handle_command("/deals")
    text = f"🧪 <b>Test message from CheapTrip</b>: deal alerts will arrive in this chat.\n\n{deals}"
    notifier = await TelegramNotifier.create()
    if await notifier.send_system_message(text):
        return True, "Test message sent: check your Telegram chat."
    return False, ("Telegram didn't accept the message (its answer is in the telegram_send_failed line above); "
                   "run `python main.py doctor` to check the token and chat.")
