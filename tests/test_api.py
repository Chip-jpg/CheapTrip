"""The desktop app's local API (B38): every screen's data, controls, settings, events, and its guard."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

import control
from api.server import TOKEN_COOKIE, create_app
from notifier.telegram import TelegramNotifier
from preferences import UserPreferences, get_preferences, save_preferences
from scheduler.engine import Engine
from scrapers.aggregator import ScraperAggregator
from storage.database import get_pref_overrides, init_db
from tests.harness import FakeFeedScraper, FakeFlightScraper, FakeHotelScraper, flight
from tests.test_engine_service import SlowFlights
from utils import airports
from utils.envfile import update_env_file

TOKEN = "t0k3n-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Api:
    def __init__(self, client: TestClient, service: Engine, engine: Any) -> None:
        self.client, self.service, self.engine = client, service, engine

    async def call(self, method: str, path: str, body: Optional[dict] = None, status: int = 200) -> Any:
        resp = await self.client.request(method, f"/api/v1{path}", json=body, headers=AUTH)
        data = await resp.json()
        assert resp.status == status, data
        return data

    async def get(self, path: str, status: int = 200) -> Any:
        return await self.call("GET", path, status=status)


async def _api(engine, flights, tmp_path, monkeypatch) -> Api:
    monkeypatch.chdir(tmp_path)  # the .env written by Settings lands here
    await init_db()
    aggregator = ScraperAggregator(
        flight_scrapers=[flights, FakeFeedScraper()], hotel_scrapers=[FakeHotelScraper()])
    service = Engine(await TelegramNotifier.create(), aggregator, telegram_commands=False)
    await service.start(first_search=False)
    client = TestClient(TestServer(create_app(service, TOKEN)))
    await client.start_server()
    return Api(client, service, engine)


FARES = [
    flight(destination="KRK", price=300.0, days_ahead=20),
    flight(destination="PRG", price=40.0, days_ahead=21),
    flight(destination="PRG", price=35.0, days_ahead=28),
    flight(destination="BCN", price=60.0, days_ahead=40, nights=6),
]


@pytest.fixture
async def api(engine, tmp_path, monkeypatch):
    engine.set_prefs(priority_destinations=["KRK"])
    api = await _api(engine, FakeFlightScraper(FARES), tmp_path, monkeypatch)
    yield api
    await api.client.close()
    await api.service.stop()


# ── Guard ─────────────────────────────────────────────────────────────────────

async def test_requests_need_the_token(api):
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        resp = await api.client.get("/api/v1/status", headers=headers)
        assert resp.status == 401


async def test_other_hosts_are_refused(api):
    resp = await api.client.get("/api/v1/status", headers={**AUTH, "Host": "evil.example"})
    assert resp.status == 403


async def test_opening_the_app_with_the_token_sets_a_cookie(api):
    resp = await api.client.get(f"/?token={TOKEN}", allow_redirects=False)
    assert resp.status == 302 and resp.headers["Location"] == "/"
    assert TOKEN_COOKIE in resp.cookies and resp.cookies[TOKEN_COOKIE]["httponly"]

    status = await api.client.get("/api/v1/status")  # the cookie alone is enough now
    assert status.status == 200

    page = await api.client.get("/")
    assert page.status == 200 and "CheapTrip" in await page.text()


# ── Status and deals ──────────────────────────────────────────────────────────

async def test_status_tracks_the_engine_and_learning(api):
    status = await api.get("/status")
    assert status["state"] == "running" and status["last_cycle"] is None and status["version"]

    await api.service.run_cycle()

    status = await api.get("/status")
    assert status["searches_today"] == 1 and status["last_cycle"]["fares"] == 4
    assert status["learning"] == {"trips": 4, "with_usual_price": 0}
    assert status["telegram_configured"] is True and status["attention"] == []


async def test_deals_split_unusually_cheap_from_the_best_of_the_rest(api):
    await control.pause(hours=1)  # keep the priority deal waiting, so it isn't sent
    await api.service.run_cycle()

    deals = await api.get("/deals")

    [krakow] = deals["instant"]
    assert (krakow["destination"]["city"], krakow["is_priority"], krakow["waiting"]) == ("Krakow", True, True)
    assert "priority destination KRK" in krakow["reasons"]
    routes = [d["route"] for d in deals["digest"]]
    assert routes == ["Milan → Prague", "Milan → Barcelona"]  # the best Prague fare only, cheapest first
    prague = deals["digest"][0]
    assert prague["price"] == 35.0 and prague["type"] == "flight" and prague["length"] == "weekend"
    assert prague["links"]["book"] and "booking.com" in prague["links"]["hotels"]
    assert deals["summary"]["unusually_cheap"] == 1
    assert deals["summary"]["best"]["id"] == krakow["id"]  # the most unusually cheap deal, in full
    assert deals["summary"]["last_search"]["fares"] == 4
    assert krakow["comparison"]["window_days"] == 30  # the card's fare range
    assert "comparison" not in prague


@pytest.mark.parametrize("query,routes", [
    ("max_price=50", ["Milan → Prague"]),
    ("lengths=short", ["Milan → Barcelona"]),
    ("q=barc", ["Milan → Barcelona"]),
    ("sort=date", ["Milan → Prague", "Milan → Barcelona"]),
    ("sort=discount", ["Milan → Prague", "Milan → Barcelona"]),
    ("priority_only=1", []),
])
async def test_deal_filters(api, query, routes):
    await api.service.run_cycle()
    deals = await api.get(f"/deals?{query}")
    assert [d["route"] for d in deals["digest"]] == routes


async def test_sent_alerts_show_when_and_where(api):
    await api.service.run_cycle()  # the priority deal is sent to Telegram (mocked)
    [krakow] = (await api.get("/deals"))["instant"]
    assert krakow["waiting"] is False and krakow["notified"]["channels"] == ["telegram"]


async def test_deal_detail_has_both_charts(api):
    await api.service.run_cycle()
    await api.service.run_cycle()
    prague = (await api.get("/deals"))["digest"][0]

    detail = await api.get(f"/deals/{prague['id']}")

    comparison = detail["comparison"]
    assert comparison["window_days"] == 30 and len(comparison["points"]) == 2
    assert [p["is_this"] for p in comparison["points"]] == [False, True]  # 21 and 28 days ahead
    assert comparison["median"] == 37.5 and len(comparison["history"]) == 2
    await api.get("/deals/nope", status=404)


async def test_hidden_deals_leave_the_list(api):
    await api.service.run_cycle()
    prague = (await api.get("/deals"))["digest"][0]

    assert (await api.call("POST", f"/deals/{prague['id']}/hide"))["hidden"] is True

    digest = (await api.get("/deals"))["digest"]
    assert prague["id"] not in [d["id"] for d in digest]
    assert next(d["price"] for d in digest if d["route"] == "Milan → Prague") == 40.0  # the other week's fare


# ── Destinations ──────────────────────────────────────────────────────────────

async def test_destinations_tabs_and_toggles(api):
    await api.service.run_cycle()

    seen = (await api.get("/destinations?tab=seen"))["destinations"]
    prague = next(d for d in seen if d["code"] == "PRG")
    assert (prague["city"], prague["best_price"], prague["fares"]) == ("Prague", 35.0, 2)

    assert (await api.call("POST", "/destinations/PRG/mute"))["is_muted"] is True
    assert [d["code"] for d in (await api.get("/destinations?tab=muted"))["destinations"]] == ["PRG"]
    assert "PRG" in get_preferences().excluded_destinations  # the same state as Telegram /mute

    await api.call("DELETE", "/destinations/KRK/priority")
    assert (await api.get("/destinations?tab=priority"))["destinations"] == []

    await api.call("POST", "/destinations/XQZ/mute", status=400)
    await api.get("/destinations?tab=nope", status=400)


async def test_airport_autocomplete(api):
    found = await api.get("/airports?q=krak")
    assert found[0]["code"] == "KRK" and found[0]["city"] == "Krakow"


# ── Activity ──────────────────────────────────────────────────────────────────

async def test_activity_lists_sources_alerts_and_searches(api):
    await api.service.run_cycle()

    activity = await api.get("/activity")

    sources = {s["id"]: s for s in activity["sources"]}
    assert sources["fake_flights"]["status"] == "OK" and sources["fake_flights"]["last_count"] == 4
    assert activity["alerts"]["per_hour_limit"] == 5 and activity["alerts"]["sent_last_hour"] == 1
    [sent] = activity["alerts"]["log"]
    assert sent["route"] == "Milan → Krakow" and sent["channel"] == "telegram"
    assert [c["status"] for c in activity["searches"]] == ["ok"]


# ── Settings ──────────────────────────────────────────────────────────────────

async def test_settings_never_send_keys_back(api):
    settings = await api.get("/settings")
    assert settings["keys"]["telegram_bot_token"] == {"set": True, "hint": "…OKEN"}
    assert settings["keys"]["anthropic_api_key"] == {"set": False, "hint": ""}
    assert settings["alerts"]["digest_time"] == "08:00"
    assert settings["preferences"]["priority_destinations"] == ["KRK"]


async def test_saving_settings_writes_env_and_preferences(api, tmp_path):
    result = await api.call("PUT", "/settings", {
        "keys": {"anthropic_api_key": "sk-ant-secret-1234"},
        "alerts": {"digest_time": "7:30", "price_anomaly_min_drop_pct": 40},
        "sources": {"enable_going": True},
        "preferences": {"home_airports": ["lgw"], "adults": 2},
    })

    assert result["applies"]["alerts.digest_time"] == "now"
    assert result["applies"]["preferences.home_airports"] == "next search"
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    for line in ("ANTHROPIC_API_KEY=sk-ant-secret-1234", "DIGEST_HOUR=7", "DIGEST_MINUTE=30",
                 "PRICE_ANOMALY_MIN_DROP_PCT=40.0", "ENABLE_GOING=true"):
        assert line in env
    settings = result["settings"]
    assert settings["keys"]["anthropic_api_key"] == {"set": True, "hint": "…1234"}
    assert settings["alerts"]["digest_time"] == "07:30"
    assert settings["preferences"]["home_airports"] == ["LGW"] and settings["preferences"]["adults"] == 2
    job = api.service._scheduler.get_job("daily_digest")
    assert str(job.trigger).startswith("cron[") and "hour='7'" in str(job.trigger)


async def test_app_settings_theme_and_close_button(api, tmp_path):
    assert (await api.get("/settings"))["app"] == {"close_to_tray": True, "theme": "system"}
    result = await api.call("PUT", "/settings", {"app": {"theme": "dark", "close_to_tray": False}})

    assert result["settings"]["app"] == {"close_to_tray": False, "theme": "dark"}
    assert result["applies"] == {"app.theme": "now", "app.close_to_tray": "now"}
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "THEME=dark" in env and "CLOSE_TO_TRAY=false" in env


async def test_saving_a_destination_list_replaces_telegram_overrides(api):
    await control.set_priority("JFK", True)  # e.g. from Telegram /priority JFK
    await api.call("PUT", "/settings", {"preferences": {"priority_destinations": ["NRT"]}})

    assert get_preferences().priority_destinations == ["NRT"]
    assert "priority_added" not in await get_pref_overrides()


@pytest.mark.parametrize("body,message", [
    ({"preferences": {"home_airports": ["XQZ"]}}, "unknown airport codes XQZ"),
    ({"preferences": {"home_airports": []}}, "at least one airport"),
    ({"preferences": {"flying_carpet": True}}, "unknown preferences"),
    ({"alerts": {"digest_time": "25:00"}}, "HH:MM"),
    ({"alerts": {"instant_alerts_per_hour": 0}}, "greater than or equal to 1"),
    ({"keys": {"database_url": "x"}}, "unknown keys"),
    ({"sources": {"enable_rockets": True}}, "unknown source switches"),
    ({"app": {"theme": "sepia"}}, "theme must be one of system, light, dark"),
    ({"app": {"close_to_tray": "yes"}}, "close_to_tray must be true or false"),
    ({"app": {"wallpaper": "x"}}, "unknown app settings"),
])
async def test_bad_settings_are_refused(api, body, message):
    error = await api.call("PUT", "/settings", body, status=400)
    assert message in error["error"]


# ── Engine controls ───────────────────────────────────────────────────────────

async def test_search_now_pause_and_resume(engine, tmp_path, monkeypatch):
    api = await _api(engine, SlowFlights(), tmp_path, monkeypatch)
    try:
        assert (await api.call("POST", "/engine/search-now"))["started"] is True
        assert (await api.call("POST", "/engine/search-now"))["started"] is False
        assert (await api.get("/status"))["state"] == "searching"

        paused = await api.call("POST", "/engine/pause", {"hours": 4})
        assert paused["paused_until"]
        tomorrow = await api.call("POST", "/engine/pause", {"until": "tomorrow"})
        assert tomorrow["paused_until"].endswith(":00:00")
        await api.call("POST", "/engine/resume")
        assert (await api.get("/status"))["paused_until"] is None
        await api.call("POST", "/engine/pause", {"days": 2}, status=400)
    finally:
        await api.client.close()
        await api.service.stop()


async def test_events_stream_live(api):
    resp = await api.client.get("/api/v1/events", headers=AUTH)
    assert resp.headers["Content-Type"] == "text/event-stream"
    assert (await resp.content.readline()).startswith(b": connected")

    await api.call("POST", "/engine/pause", {"hours": 1})
    lines = []
    while not any(line.startswith("data:") for line in lines):
        lines.append((await asyncio.wait_for(resp.content.readline(), timeout=5)).decode().strip())

    assert "event: paused" in lines
    data = json.loads(next(line for line in lines if line.startswith("data:"))[5:])
    assert data["type"] == "paused" and data["until"]
    resp.close()


# ── Settings files ────────────────────────────────────────────────────────────

def test_env_updates_keep_everything_else(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# Telegram\nTELEGRAM_BOT_TOKEN=old\nOTHER=1\nTELEGRAM_BOT_TOKEN=dup\nREMOVE_ME=x\n",
                   encoding="utf-8")

    update_env_file(env, {"telegram_bot_token": "new", "remove_me": None, "NEW_ONE": "has space"})

    assert env.read_text(encoding="utf-8").splitlines() == [
        "# Telegram", "TELEGRAM_BOT_TOKEN=new", "OTHER=1", 'NEW_ONE="has space"',
    ]


def test_saving_preferences_keeps_the_comments(tmp_path):
    path = tmp_path / "prefs.yaml"
    path.write_text("# Where you fly from\nhome_airports:\n  - MXP  # Malpensa\nadults: 1  # travellers\n",
                    encoding="utf-8")

    save_preferences({"adults": 2}, path)

    text = path.read_text(encoding="utf-8")
    assert "# Where you fly from" in text and "# Malpensa" in text and "adults: 2" in text
    with pytest.raises(ValueError):
        save_preferences({"adults": "many"}, path)
    assert UserPreferences.model_fields  # sanity: the model is what validates


def test_saving_the_documented_preferences_changes_only_what_changed(tmp_path):
    """The example file the installer writes: every setting's description must survive a save."""
    example = Path(__file__).resolve().parent.parent / "config" / "user_preferences.yaml.example"
    original = example.read_text(encoding="utf-8")
    path = tmp_path / "prefs.yaml"
    path.write_text(original, encoding="utf-8")
    values = yaml.safe_load(original)

    save_preferences({k: values[k] for k in ("home_airports", "preferred_trip_lengths", "max_trip_budget")}, path)
    assert path.read_text(encoding="utf-8") == original  # the Settings screen re-sent unchanged values

    save_preferences({"home_airports": ["LGW"], "max_trip_budget": 300, "repositioning_hubs": [],
                      "excluded_destinations": ["KRK"]}, path)
    changed = path.read_text(encoding="utf-8")
    comments = [line for line in original.splitlines() if line.lstrip().startswith("#")]
    assert [line for line in changed.splitlines() if line.lstrip().startswith("#")] == comments
    assert "home_airports:\n  - LGW\n" in changed and "repositioning_hubs: []" in changed

    save_preferences({"max_trip_budget": None, **{k: values[k] for k in (
        "home_airports", "repositioning_hubs", "excluded_destinations")}}, path)
    assert path.read_text(encoding="utf-8") == original  # and back again, exactly


def test_airport_names_and_search():
    assert airports.display_name("MIL") == "Milan" and airports.display_name("MXP") == "Milan"
    assert airports.display_name("KRK") == "Krakow"
    assert [a.iata for a in airports.search("jfk")][:1] == ["JFK"]
    assert "LHR" in [a.iata for a in airports.search("London")]
    assert airports.search("") == []


def test_every_source_switch_exists_in_settings():
    from api.server import SWITCH_OF
    from config import Settings

    assert set(SWITCH_OF.values()) <= set(Settings.model_fields)


def test_ui_files_are_optional():
    from api.server import ui_dir

    path = ui_dir()
    assert path is None or (Path(path) / "index.html").exists()
