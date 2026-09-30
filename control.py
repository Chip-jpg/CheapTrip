"""
Engine controls shared by the Telegram commands and the desktop app (B37).

Mute, priority and budget are preference overrides (the pref_overrides table,
merged over config/user_preferences.yaml by preferences.apply_overrides);
pause is the engine_state "paused_until" value. Telegram's /mute and the app's
Mute button call the same functions, so they always agree. Every change is
published on the event bus so open screens and the tray update at once.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from preferences import apply_overrides, load_preferences, preferences_path
from storage.database import get_pref_overrides, get_state, set_pref_override, set_state
from utils import airports
from utils.events import get_event_bus
from utils.timeutil import utcnow

PAUSED_UNTIL_KEY = "paused_until"
LAST_CYCLE_KEY = "last_cycle_at"
DEFAULT_PAUSE_HOURS = 24
PAUSE_INDEFINITELY_DAYS = 3650  # "until I resume"
MORNING_HOUR = 8  # "until tomorrow" ends at 08:00 local time


# ── Pause ─────────────────────────────────────────────────────────────────────

async def paused_until() -> Optional[datetime]:
    """When alerts resume, if they are paused now."""
    value = await get_state(PAUSED_UNTIL_KEY)
    if not value:
        return None
    until = datetime.fromisoformat(value)
    return until if until > utcnow() else None


async def pause(hours: Optional[float] = None, until: Optional[datetime] = None) -> datetime:
    """Hold alerts for `hours` (default 24) or until a time (naive UTC); searching continues."""
    if until is None:
        hours = DEFAULT_PAUSE_HOURS if hours is None else hours
        if hours <= 0:
            raise ValueError("pause hours must be positive")
        until = utcnow() + timedelta(hours=hours)
    elif until <= utcnow():
        raise ValueError("pause end must be in the future")
    await set_state(PAUSED_UNTIL_KEY, until.isoformat())
    get_event_bus().publish("paused", until=until.isoformat())
    return until


async def resume() -> None:
    await set_state(PAUSED_UNTIL_KEY, None)
    get_event_bus().publish("resumed")


def pause_end(hours: Optional[float] = None, until: Optional[str] = None) -> Dict[str, Any]:
    """
    The app's pause choices as pause() arguments: {"hours": n}, "tomorrow"
    (08:00 local time tomorrow) or "resume" (until the user resumes).
    """
    if hours is not None:
        return {"hours": float(hours)}
    if until == "tomorrow":
        from config import get_settings

        tz = ZoneInfo(get_settings().timezone)
        morning = (datetime.now(tz) + timedelta(days=1)).replace(hour=MORNING_HOUR, minute=0, second=0, microsecond=0)
        return {"until": morning.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)}
    if until == "resume":
        return {"hours": PAUSE_INDEFINITELY_DAYS * 24}
    raise ValueError('pause needs {"hours": n}, {"until": "tomorrow"} or {"until": "resume"}')


# ── Preference overrides ──────────────────────────────────────────────────────

async def load_overrides() -> None:
    """Apply the stored overrides to get_preferences() (called at every cycle start and after a change)."""
    apply_overrides(await get_pref_overrides())


def airport_code(value: str) -> Optional[str]:
    """A known IATA code from user input ("krk " -> "KRK"), or None."""
    code = (value or "").strip().upper()
    return code if len(code) == 3 and airports.is_known(code) else None


async def _toggle(list_key: str, undo_key: str, yaml_list: List[str], code: str, on: bool) -> None:
    """Add (on) or remove a code via overrides, relative to the YAML list."""
    overrides = await get_pref_overrides()
    added, removed = list(overrides.get(list_key, [])), list(overrides.get(undo_key, []))
    if on:
        removed = [c for c in removed if c != code]
        if code not in yaml_list and code not in added:
            added.append(code)
    else:
        added = [c for c in added if c != code]
        if code in yaml_list and code not in removed:
            removed.append(code)
    await set_pref_override(list_key, added or None)
    await set_pref_override(undo_key, removed or None)
    await load_overrides()


async def _set_muted(code: str, muted: bool) -> None:
    await _toggle("muted", "unmuted", load_preferences(preferences_path()).excluded_destinations, code, muted)


async def _set_priority(code: str, priority: bool) -> None:
    await _toggle("priority_added", "priority_removed", load_preferences(preferences_path()).priority_destinations,
                  code, priority)


async def set_muted(code: str, muted: bool) -> None:
    """Never alert this destination (muted, which ends its priority) or alert it again."""
    if muted:
        await _set_priority(code, False)
    await _set_muted(code, muted)
    get_event_bus().publish("preferences_changed", change="muted" if muted else "unmuted", code=code)


async def set_priority(code: str, priority: bool) -> None:
    """Always alert this destination (priority, which unmutes it), or stop doing so."""
    if priority:
        await _set_muted(code, False)
    await _set_priority(code, priority)
    get_event_bus().publish("preferences_changed", change="priority" if priority else "unpriority", code=code)


async def set_budget(amount: Optional[float]) -> None:
    """Skip trips above `amount` euros; None removes the limit."""
    if amount is not None and amount <= 0:
        raise ValueError("the budget must be a positive amount")
    await set_pref_override("max_trip_budget", "off" if amount is None else amount)
    await load_overrides()
    get_event_bus().publish("preferences_changed", change="budget", amount=amount)
