"""
The Settings screen's data (B38): read the effective settings, and apply
changes to `.env` (keys, alert rules, source switches) and to the preferences
YAML (trips, destinations), then to the running engine without a restart.

Notification switches (desktop, Telegram, which kinds of message, sound) are
read at every send, so they apply at once.

Keys are never sent back in full: the screen gets {"set": true, "hint": "…1234"}.
In an update a key's value "" clears it and a missing key is left alone.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

import control
from config import get_settings
from preferences import get_preferences, preferences_path, save_preferences
from storage.database import set_pref_override
from utils import airports
from utils.envfile import update_env_file
from utils.events import get_event_bus

SECRET_KEYS = ("telegram_bot_token", "anthropic_api_key", "travelpayouts_token", "rapidapi_key")
PLAIN_KEYS = ("telegram_chat_id",)
TELEGRAM_KEYS = ("telegram_bot_token", "telegram_chat_id")
SOURCE_SWITCHES = (
    "enable_ryanair", "enable_piratinviaggio", "enable_skyscanner", "enable_google_hotels",
    "enable_booking_html", "enable_secret_flying", "enable_going", "enable_holiday_pirates",
)
# Settings → Notifications: NOTIFY_<NAME> in .env
NOTIFY_SWITCHES = ("desktop", "telegram", "instant", "digest", "source_problems", "sound")
# Settings → App: behaviour of the desktop app's window (switches), and its colours
APP_SWITCHES = ("close_to_tray",)
THEMES = ("system", "light", "dark")
APP_SETTINGS = (*APP_SWITCHES, "theme")
# Preference lists that Telegram /mute and /priority (and /budget) override: saving them
# in Settings makes the saved value the truth again
_OVERRIDES_OF = {
    "priority_destinations": ("priority_added", "priority_removed"),
    "excluded_destinations": ("muted", "unmuted"),
    "max_trip_budget": ("max_trip_budget",),
}
_AIRPORT_LISTS = ("home_airports", "priority_destinations", "excluded_destinations", "repositioning_hubs")


class AlertRules(BaseModel):
    europe_trip_max_eur: Optional[float] = Field(None, ge=0, le=1000)
    longhaul_trip_max_eur: Optional[float] = Field(None, ge=0, le=5000)
    price_anomaly_min_drop_pct: Optional[float] = Field(None, ge=5, le=95)
    instant_alerts_per_hour: Optional[int] = Field(None, ge=1, le=60)
    digest_time: Optional[str] = None  # "HH:MM", local time
    scrape_interval_minutes: Optional[int] = Field(None, ge=30, le=1440)

    @field_validator("digest_time")
    @classmethod
    def _hh_mm(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        hour, _, minute = value.partition(":")
        if not (hour.isdigit() and minute.isdigit() and 0 <= int(hour) < 24 and 0 <= int(minute) < 60):
            raise ValueError("digest_time must be HH:MM")
        return f"{int(hour):02d}:{int(minute):02d}"


class SettingsUpdate(BaseModel):
    preferences: Optional[Dict[str, Any]] = None
    alerts: Optional[AlertRules] = None
    keys: Optional[Dict[str, Optional[str]]] = None
    sources: Optional[Dict[str, bool]] = None
    notifications: Optional[Dict[str, bool]] = None
    app: Optional[Dict[str, Any]] = None

    @field_validator("notifications")
    @classmethod
    def _known_switches(cls, value: Optional[Dict[str, bool]]) -> Optional[Dict[str, bool]]:
        unknown = set(value or {}) - set(NOTIFY_SWITCHES)
        if unknown:
            raise ValueError(f"unknown notification switches: {', '.join(sorted(unknown))}")
        return value

    @field_validator("app")
    @classmethod
    def _known_app_settings(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        unknown = set(value or {}) - set(APP_SETTINGS)
        if unknown:
            raise ValueError(f"unknown app settings: {', '.join(sorted(unknown))}")
        for name, setting in (value or {}).items():
            if name in APP_SWITCHES and not isinstance(setting, bool):
                raise ValueError(f"{name} must be true or false")
        if value and "theme" in value and value["theme"] not in THEMES:
            raise ValueError(f"theme must be one of {', '.join(THEMES)}")
        return value

    @field_validator("keys")
    @classmethod
    def _known_keys(cls, value: Optional[Dict[str, Optional[str]]]) -> Optional[Dict[str, Optional[str]]]:
        unknown = set(value or {}) - set(SECRET_KEYS) - set(PLAIN_KEYS)
        if unknown:
            raise ValueError(f"unknown keys: {', '.join(sorted(unknown))}")
        return value

    @field_validator("sources")
    @classmethod
    def _known_sources(cls, value: Optional[Dict[str, bool]]) -> Optional[Dict[str, bool]]:
        unknown = set(value or {}) - set(SOURCE_SWITCHES)
        if unknown:
            raise ValueError(f"unknown source switches: {', '.join(sorted(unknown))}")
        return value


def env_path() -> Path:
    """The .env the settings are read from (the working folder: the app home in the Windows app)."""
    return Path.cwd() / ".env"


def _secret(value: str) -> Dict[str, Any]:
    return {"set": bool(value), "hint": ("…" + value[-4:]) if len(value) > 8 else ("set" if value else "")}


def read_settings() -> Dict[str, Any]:
    settings = get_settings()
    return {
        "preferences": get_preferences().model_dump(),
        "alerts": {
            "europe_trip_max_eur": settings.europe_trip_max_eur,
            "longhaul_trip_max_eur": settings.longhaul_trip_max_eur,
            "price_anomaly_min_drop_pct": settings.price_anomaly_min_drop_pct,
            "instant_alerts_per_hour": settings.instant_alerts_per_hour,
            "digest_time": f"{settings.digest_hour:02d}:{settings.digest_minute:02d}",
            "scrape_interval_minutes": settings.scrape_interval_minutes,
        },
        "keys": {
            **{name: _secret(getattr(settings, name) or "") for name in SECRET_KEYS},
            **{name: getattr(settings, name) or "" for name in PLAIN_KEYS},
        },
        "sources": {name: getattr(settings, name) for name in SOURCE_SWITCHES},
        "notifications": {
            **{name: getattr(settings, f"notify_{name}") for name in NOTIFY_SWITCHES},
            "telegram_ready": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        },
        "app": {
            **{name: getattr(settings, name) for name in APP_SWITCHES},
            "theme": settings.theme if settings.theme in THEMES else "system",
        },
        "files": {"settings": str(env_path()), "preferences": str(preferences_path())},
    }


def _check_airports(prefs: Dict[str, Any]) -> None:
    for field in _AIRPORT_LISTS:
        if field in prefs and prefs[field] is not None:
            codes = [str(code).strip().upper() for code in prefs[field]]
            unknown = [code for code in codes if not airports.is_known(code)]
            if unknown:
                raise ValueError(f"{field}: unknown airport codes {', '.join(unknown)}")
            prefs[field] = codes
    if prefs.get("home_airports") == []:
        raise ValueError("home_airports: at least one airport is needed")


def _env_value(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return "true" if value else "false"
    return None if value is None else str(value)


async def apply_settings(payload: Dict[str, Any], engine: Any = None) -> Dict[str, Any]:
    """
    Validate and save a settings update, then apply it to the running engine.
    Returns {"saved": [...fields], "applies": {field: "now" | "next search"}}.
    Raises ValueError (pydantic's ValidationError included) on bad input.
    """
    update = SettingsUpdate(**payload)
    saved: List[str] = []
    applies: Dict[str, str] = {}
    env: Dict[str, Optional[str]] = {}

    if update.preferences:
        prefs = dict(update.preferences)
        _check_airports(prefs)
        save_preferences(prefs)
        for field in prefs:
            for override in _OVERRIDES_OF.get(field, ()):
                await set_pref_override(override, None)
            saved.append(f"preferences.{field}")
            applies[f"preferences.{field}"] = "next search"
        await control.load_overrides()

    if update.alerts:
        for field, value in update.alerts.model_dump(exclude_none=True).items():
            if field == "digest_time":
                hour, minute = value.split(":")
                env["DIGEST_HOUR"], env["DIGEST_MINUTE"] = str(int(hour)), str(int(minute))
            else:
                env[field.upper()] = _env_value(value)
            saved.append(f"alerts.{field}")
            applies[f"alerts.{field}"] = "now" if field in (
                "digest_time", "scrape_interval_minutes", "instant_alerts_per_hour") else "next search"

    for name, value in (update.keys or {}).items():
        env[name.upper()] = (value or "").strip()
        saved.append(f"keys.{name}")
        applies[f"keys.{name}"] = "now" if name in TELEGRAM_KEYS else "next search"

    for name, on in (update.sources or {}).items():
        env[name.upper()] = _env_value(on)
        saved.append(f"sources.{name}")
        applies[f"sources.{name}"] = "next search"

    for name, on in (update.notifications or {}).items():
        env[f"NOTIFY_{name.upper()}"] = _env_value(on)
        saved.append(f"notifications.{name}")
        applies[f"notifications.{name}"] = "now"

    for name, value in (update.app or {}).items():
        env[name.upper()] = _env_value(value)
        saved.append(f"app.{name}")
        applies[f"app.{name}"] = "now"

    if env:
        update_env_file(env_path(), env)
        for key, value in env.items():  # the running process: an env var would otherwise win over .env
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    if engine is not None and saved:
        await engine.apply_settings_change(
            reschedule=any(f in saved for f in ("alerts.digest_time", "alerts.scrape_interval_minutes")),
            reload_notifier=any(f in saved for f in (
                "keys.telegram_bot_token", "keys.telegram_chat_id", "alerts.instant_alerts_per_hour")),
            reload_sources=any(f.startswith(("sources.", "keys.")) for f in saved)
            or "preferences.home_airports" in saved,
        )
    if saved:
        get_event_bus().publish("settings_changed", fields=saved)
    return {"saved": saved, "applies": applies}
