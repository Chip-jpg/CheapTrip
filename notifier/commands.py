"""
Telegram commands (B20): steer the bot from the chat it alerts.

A long-polling loop (getUpdates) runs next to the scheduler in `main.py run`.
Only messages from TELEGRAM_CHAT_ID are accepted; everything else is
ignored. The update offset is kept in the engine_state table, so a restart
never replays a command.

Commands change preference overrides or the pause state through control.py,
the same functions the desktop app uses:

  /deals              top deals of the last 24 hours, one per route
  /status             last cycle, deals and alerts today, pause and overrides
  /health             source health
  /mute KRK           never alert this destination     (/unmute KRK)
  /priority KRK       always alert this destination    (/unpriority KRK)
  /budget 300         skip trips above €300            (/budget off)
  /pause [hours]      hold alerts (default 24h); cycles keep running
  /resume             send alerts again
"""
from __future__ import annotations

import asyncio
import html
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

import control
from ai_layer.formatter import digest_lines
from config import get_settings
from control import (  # noqa: F401 (re-exported: older imports use notifier.commands)
    DEFAULT_PAUSE_HOURS,
    LAST_CYCLE_KEY,
    PAUSED_UNTIL_KEY,
    load_overrides,
    paused_until,
)
from notifier.telegram import TelegramNotifier
from preferences import get_preferences
from storage.database import (
    count_deals_since,
    get_digest_deals,
    get_recent_alert_timestamps,
    get_state,
    set_state,
)
from storage.models import AlertTier
from utils import airports
from utils.logging_config import get_logger
from utils.timeutil import utcnow

log = get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
POLL_TIMEOUT_S = 25
_OFFSET_KEY = "telegram_update_offset"

HELP = (
    "<b>Commands</b>\n"
    "/deals — top deals of the last 24h\n"
    "/status — last cycle, alerts today, settings\n"
    "/health — source health\n"
    "/mute KRK · /unmute KRK — never / again alert a destination\n"
    "/priority KRK · /unpriority KRK — always alert a destination\n"
    "/budget 300 · /budget off — maximum trip price\n"
    "/pause [hours] · /resume — hold alerts (default 24h)"
)


# ── Commands ──────────────────────────────────────────────────────────────────

def _code(args: List[str]) -> Optional[str]:
    return control.airport_code(args[0]) if args else None


def _place(code: str) -> str:
    return f"{html.escape(airports.city_of(code))} ({code})"


async def _cmd_help(args: List[str]) -> str:
    return HELP


async def _cmd_deals(args: List[str]) -> str:
    from scheduler.runner import select_digest_trips

    trips = select_digest_trips(await get_digest_deals(limit=100))[:10]
    if not trips:
        return "No deals in the last 24 hours yet."
    return "\n".join(["🔎 <b>Top deals, last 24h</b>", ""] + digest_lines(trips))


async def _cmd_status(args: List[str]) -> str:
    prefs = get_preferences()
    last = await get_state(LAST_CYCLE_KEY)
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = len(await get_recent_alert_timestamps(AlertTier.INSTANT, within_hours=24))
    until = await paused_until()
    lines = [
        "📊 <b>Status</b>",
        f"Last cycle: {last[:16].replace('T', ' ') + ' UTC' if last else 'not yet'}",
        f"Deals saved today: {await count_deals_since(midnight)}",
        f"Instant alerts, last 24h: {sent_today}",
        f"Alerts: {'paused until ' + f'{until:%d %b %H:%M} UTC' if until else 'on'}",
        f"Muted: {', '.join(prefs.excluded_destinations) or 'none'}",
        f"Priority: {', '.join(prefs.priority_destinations) or 'none'}",
        f"Budget: {'€%.0f' % prefs.max_trip_budget if prefs.max_trip_budget else 'no limit'}",
    ]
    return "\n".join(lines)


async def _cmd_health(args: List[str]) -> str:
    from scrapers.health_monitor import get_health_monitor

    summary = await get_health_monitor().get_summary()
    return f"<pre>{html.escape(summary)}</pre>"


async def _cmd_mute(args: List[str], on: bool = True) -> str:
    code = _code(args)
    if not code:
        return f"Usage: /{'mute' if on else 'unmute'} KRK (an airport code)"
    await control.set_muted(code, on)
    return f"🔇 Muted {_place(code)}: no more alerts." if on else f"🔔 {_place(code)} is back in your alerts."


async def _cmd_priority(args: List[str], on: bool = True) -> str:
    code = _code(args)
    if not code:
        return f"Usage: /{'priority' if on else 'unpriority'} KRK (an airport code)"
    await control.set_priority(code, on)
    if on:
        return f"⭐ {_place(code)} is a priority: every deal there alerts instantly."
    return f"{_place(code)} is no longer a priority."


async def _cmd_budget(args: List[str]) -> str:
    if not args:
        return "Usage: /budget 300 or /budget off"
    if args[0].lower() == "off":
        await control.set_budget(None)
        return "💶 No budget limit."
    try:
        amount = float(args[0].replace("€", "").replace(",", "."))
    except ValueError:
        return "Usage: /budget 300 or /budget off"
    if amount <= 0:
        return "The budget must be a positive amount."
    await control.set_budget(amount)
    return f"💶 Trips above €{amount:.0f} are skipped."


async def _cmd_pause(args: List[str]) -> str:
    try:
        hours = float(args[0]) if args else DEFAULT_PAUSE_HOURS
    except ValueError:
        return "Usage: /pause or /pause 12 (hours)"
    if hours <= 0:
        return "Usage: /pause or /pause 12 (hours)"
    until = await control.pause(hours)
    return f"⏸ Alerts paused until {until:%d %b %H:%M} UTC. Searching continues; /resume to undo."


async def _cmd_resume(args: List[str]) -> str:
    await control.resume()
    return "▶️ Alerts are back on."


async def _cmd_unmute(args: List[str]) -> str:
    return await _cmd_mute(args, on=False)


async def _cmd_unpriority(args: List[str]) -> str:
    return await _cmd_priority(args, on=False)


COMMANDS: Dict[str, Callable[[List[str]], Awaitable[str]]] = {
    "help": _cmd_help, "start": _cmd_help,
    "deals": _cmd_deals, "status": _cmd_status, "health": _cmd_health,
    "mute": _cmd_mute, "unmute": _cmd_unmute,
    "priority": _cmd_priority, "unpriority": _cmd_unpriority,
    "budget": _cmd_budget, "pause": _cmd_pause, "resume": _cmd_resume,
}


async def handle_command(text: str) -> Optional[str]:
    """Reply for one message, or None when it isn't a command."""
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0][1:].split("@", 1)[0].lower()  # "/mute@CheapTripBot" in groups
    handler = COMMANDS.get(name)
    if handler is None:
        return f"Unknown command /{html.escape(name)}. Try /help."
    try:
        return await handler(parts[1:])
    except Exception as exc:
        log.error("command_failed", command=name, error=str(exc), exc_info=True)
        return "Sorry, that command failed. Check the logs."


# ── Polling ───────────────────────────────────────────────────────────────────

class CommandBot:
    """Polls Telegram for commands from the configured chat and replies to them."""

    def __init__(self, notifier: TelegramNotifier) -> None:
        settings = get_settings()
        self._notifier = notifier
        self._token = settings.telegram_bot_token
        self._chat_id = str(settings.telegram_chat_id)
        self.enabled = bool(self._token and self._chat_id)
        self._webhook_warned = False

    async def poll_once(self, client: httpx.AsyncClient, timeout: int = POLL_TIMEOUT_S) -> int:
        """Fetch pending updates, answer commands; returns how many updates were handled."""
        offset = int(await get_state(_OFFSET_KEY) or 0)
        resp = await client.get(
            f"{_TELEGRAM_API}/bot{self._token}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": '["message"]'},
        )
        if resp.status_code == 409:
            if not self._webhook_warned:
                log.warning("telegram_commands_webhook_conflict",
                            hint="a webhook is set for this bot; commands need getUpdates")
                self._webhook_warned = True
            return 0
        resp.raise_for_status()
        updates: List[Dict[str, Any]] = resp.json().get("result") or []
        for update in updates:
            await set_state(_OFFSET_KEY, str(update["update_id"] + 1))  # never replay, even on failure
            message = update.get("message") or {}
            if str((message.get("chat") or {}).get("id")) != self._chat_id:
                log.info("telegram_command_ignored_other_chat")
                continue
            reply = await handle_command(message.get("text") or "")
            if reply:
                await self._notifier.send_system_message(reply)
        return len(updates)

    async def run(self, stop: asyncio.Event) -> None:
        if not self.enabled:
            return
        log.info("telegram_commands_started")
        backoff = 5.0
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT_S + 10) as client:
            while not stop.is_set():
                try:
                    await self.poll_once(client)
                    backoff = 5.0
                    if self._webhook_warned:
                        await asyncio.wait_for(stop.wait(), timeout=300)
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    log.warning("telegram_commands_poll_failed", error=str(exc))
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=backoff)
                    except asyncio.TimeoutError:
                        pass
                    backoff = min(backoff * 2, 300.0)
        log.info("telegram_commands_stopped")
