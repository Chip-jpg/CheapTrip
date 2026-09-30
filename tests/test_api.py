"""The desktop app's local API (B38): every screen's data, controls, settings, events, and its guard."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

import aiosqlite
import httpx
import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

import control
from api.server import COMPARED_CARDS, RELEASES_API, TOKEN_COOKIE, create_app
from notifier.telegram import TelegramNotifier
from preferences import UserPreferences, get_preferences, save_preferences
from scheduler.engine import Engine
from scrapers.aggregator import ScraperAggregator
from storage.database import get_db_path, get_pref_overrides, init_db
from tests.harness import FakeFeedScraper, FakeFlightScraper, FakeHotelScraper, flight, mock_doctor_telegram
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


PAGE = """<!doctype html>
<html lang="en">
  <head><script>/* guesses the theme when <html> has none */</script></head>
  <body><div id="root"></div></body>
</html>
"""


@pytest.fixture
async def page(engine, tmp_path, monkeypatch):
    """The API with a built UI to serve (index.html plus one asset)."""
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(PAGE, encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    await init_db()
    service = Engine(await TelegramNotifier.create(), ScraperAggregator(flight_scrapers=[FakeFlightScraper([])]),
                     telegram_commands=False)
    client = TestClient(TestServer(create_app(service, TOKEN, static_dir=static)))
    await client.start_server()
    yield Api(client, service, engine)
    await client.close()


async def _html(api: Api, path: str = "/") -> str:
    resp = await api.client.get(path, headers=AUTH)
    assert resp.status == 200 and resp.headers["Content-Type"].startswith("text/html")
    return await resp.text()


@pytest.mark.parametrize("theme,mark", [("dark", '<html data-theme="dark" lang="en">'),
                                        ("light", '<html data-theme="light" lang="en">'),
                                        ("system", '<html lang="en">')])
async def test_the_page_opens_in_the_chosen_theme(page, monkeypatch, theme, mark):
    await page.call("PUT", "/settings", {"app": {"theme": theme}})

    for path in ("/", "/index.html", "/deals"):  # client-side routes get the same page
        assert mark in await _html(page, path)
    assert (await (await page.client.get("/assets/app.js")).text()) == "console.log('app')"


async def test_the_sidebar_opens_as_it_was_left(page):
    assert "data-sidebar" not in await _html(page)

    assert await page.call("PUT", "/ui", {"sidebar": "collapsed"}) == {"sidebar": "collapsed"}
    assert '<html data-sidebar="collapsed" lang="en">' in await _html(page)

    await page.call("PUT", "/ui", {"sidebar": "open"})
    assert "data-sidebar" not in await _html(page)
    await page.call("PUT", "/ui", {"sidebar": "sideways"}, status=400)


async def test_the_page_opens_even_if_the_sidebar_state_cant_be_read(page, monkeypatch):
    async def broken(key):
        raise RuntimeError("database is locked")

    monkeypatch.setattr("api.server.get_state", broken)
    assert '<html lang="en">' in await _html(page)


async def test_the_page_still_takes_the_token(page):
    resp = await page.client.get(f"/?token={TOKEN}", allow_redirects=False)
    assert resp.status == 302 and TOKEN_COOKIE in resp.cookies


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


async def test_only_the_full_cards_get_their_fare_range(engine, tmp_path, monkeypatch):
    codes = ["KRK", "PRG", "BCN", "BUD", "VIE", "WAW", "ATH", "LIS"]
    engine.set_prefs(priority_destinations=codes)
    fares = [flight(destination=code, price=100.0 + i, days_ahead=20 + i) for i, code in enumerate(codes)]
    api = await _api(engine, FakeFlightScraper(fares), tmp_path, monkeypatch)
    try:
        await control.pause(hours=1)
        await api.service.run_cycle()

        instant = (await api.get("/deals"))["instant"]

        assert len(instant) == 8
        assert all(card["comparison"]["window_days"] == 30 for card in instant[:COMPARED_CARDS])
        assert all("comparison" not in card for card in instant[COMPARED_CARDS:])  # the small cards show none
    finally:
        await api.client.close()
        await api.service.stop()


async def test_the_summary_alone_skips_the_cards(api):
    await control.pause(hours=1)
    await api.service.run_cycle()

    full, summary = await api.get("/deals"), await api.get("/deals?summary=1")

    assert summary["summary"]["unusually_cheap"] == full["summary"]["unusually_cheap"] == 1
    assert summary["summary"]["best"]["id"] == full["summary"]["best"]["id"]
    assert summary["instant"] == [] and summary["digest"] == []  # no cards, so no price comparisons


async def test_similar_fares_are_looked_up_by_index():
    await init_db()
    async with aiosqlite.connect(await get_db_path()) as db:
        cursor = await db.execute(
            "EXPLAIN QUERY PLAN SELECT price_eur FROM price_history"
            " WHERE origin_city = ? AND dest_city = ? AND trip_type = ? AND nights_bucket = ?"
            " AND recorded_at >= ? AND depart_date IS NOT NULL ORDER BY recorded_at, id",
            ("Milan", "Prague", "flight", "weekend", "2026-09-01"))
        plan = " ".join(str(row[-1]) for row in await cursor.fetchall())
    assert "idx_prices_key" in plan


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

    assert (await api.call("DELETE", f"/deals/{prague['id']}/hide"))["hidden"] is False  # Undo
    assert prague["id"] in [d["id"] for d in (await api.get("/deals"))["digest"]]
    await api.call("DELETE", "/deals/nope/hide", status=404)


# ── Destinations ──────────────────────────────────────────────────────────────

async def test_destinations_tabs_and_toggles(api):
    await api.service.run_cycle()

    seen = (await api.get("/destinations?tab=seen"))["destinations"]
    prague = next(d for d in seen if d["code"] == "PRG")
    assert (prague["city"], prague["best_price"], prague["fares"]) == ("Prague", 35.0, 2)
    assert prague["best_route"].endswith("-PRG") and prague["best_return"]
    assert [point["price"] for point in prague["trend"]] == [35.0]  # the day's cheapest fare

    assert (await api.call("POST", "/destinations/PRG/mute"))["is_muted"] is True
    assert [d["code"] for d in (await api.get("/destinations?tab=muted"))["destinations"]] == ["PRG"]
    assert "PRG" in get_preferences().excluded_destinations  # the same state as Telegram /mute

    [krakow] = (await api.get("/destinations?tab=priority"))["destinations"]
    assert krakow["is_priority"] and "trend" in krakow
    await api.call("DELETE", "/destinations/KRK/priority")
    assert (await api.get("/destinations?tab=priority"))["destinations"] == []

    await api.call("POST", "/destinations/XQZ/mute", status=400)
    await api.get("/destinations?tab=nope", status=400)


async def test_destination_figures_are_worked_out_once_per_change(api, monkeypatch):
    import api.server as server

    calls, figures, utcnow = [], server.destination_figures, server.utcnow

    async def counting(since):
        calls.append(since)
        return await figures(since)

    monkeypatch.setattr(server, "destination_figures", counting)

    await api.service.run_cycle()  # the search's end has them worked out in the background
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.02)
    assert len(calls) == 1
    await api.get("/destinations?tab=seen")
    await api.get("/destinations?tab=priority")
    assert len(calls) == 1  # opening the screen didn't wait for them

    await api.call("POST", "/data/clear-price-history")
    assert (await api.get("/destinations?tab=seen"))["destinations"] == []
    assert len(calls) == 2  # the history changed

    monkeypatch.setattr(server, "utcnow", lambda: utcnow() + timedelta(days=1))
    await api.get("/destinations?tab=seen")
    assert len(calls) == 3  # a new day moves the 30-day window


async def test_destination_figures_match_adding_up_every_price(engine):
    """B46: the database sums the Destinations figures up; they're the same as from every row."""
    import random
    import statistics
    from datetime import datetime

    from api.records import destination_rows
    from storage.database import destination_figures

    rng, now = random.Random(82), datetime.utcnow()
    rows = []
    for c in range(60):
        at = now - timedelta(hours=11 * c)
        stamp = (at if c % 6 else at.replace(microsecond=0)).isoformat()
        for route in ("MXP-KRK", "BGY-KRK", "MXP-PRG", "LIN-BCN"):
            for _ in range(rng.randint(0, 6)):
                depart = datetime(2026, 11, 1) + timedelta(days=rng.randint(0, 20))
                ret = (depart + timedelta(days=3)).date().isoformat() if rng.random() < 0.8 else None
                rows.append((route, round(rng.uniform(20, 300), 2), "x", stamp, depart.date().isoformat(), ret))
    await init_db()
    async with aiosqlite.connect(await get_db_path()) as db:
        await db.executemany("INSERT INTO price_history (route, price_eur, source, recorded_at, depart_date,"
                             " return_date) VALUES (?, ?, ?, ?, ?, ?)", rows)
        await db.commit()
        since = now - timedelta(days=30)
        cursor = await db.execute(
            "SELECT route, depart_date, COALESCE(return_date, ''), price_eur, recorded_at FROM price_history"
            " WHERE recorded_at >= ? AND depart_date IS NOT NULL ORDER BY recorded_at, id", (since.isoformat(),))
        every_row = await cursor.fetchall()

    # The reference: how v0.8.1 added them up from every row
    latest, daily = {}, {}
    for route, depart, ret, price, recorded_at in every_row:
        dest = route.split("-", 1)[1]
        latest.setdefault(dest, {})[f"{route} {depart} {ret}"] = (price, depart, recorded_at, route, ret)
        days = daily.setdefault(dest, {})
        days[recorded_at[:10]] = min(price, days.get(recorded_at[:10], price))

    mine = destination_rows(*await destination_figures(since))

    assert set(mine) == set(latest) == {"KRK", "PRG", "BCN"}
    for dest, fares in latest.items():
        best = min(fares.values())
        assert (mine[dest]["best_price"], mine[dest]["best_depart"], mine[dest]["best_route"]) == \
               (best[0], best[1], best[3])
        assert mine[dest]["usual_price"] == round(statistics.median(f[0] for f in fares.values()), 2)
        assert mine[dest]["fares"] == len(fares)
        assert mine[dest]["last_seen"] == max(f[2] for f in fares.values())
        assert mine[dest]["trend"] == [{"day": d, "price": p} for d, p in sorted(daily[dest].items())]


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


async def test_the_screens_open_the_log_and_data_folders(api, monkeypatch):
    opened = []
    monkeypatch.setattr("api.server.open_path", opened.append)
    assert (await api.call("POST", "/app/open", {"what": "logs"}))["opened"].endswith("logs")
    await api.call("POST", "/app/open", {"what": "data"})
    await api.call("POST", "/app/open", {"what": "C:/Windows"}, status=400)
    assert len(opened) == 2 and opened[1] == os.getcwd()


async def test_app_settings_theme_and_close_button(api, tmp_path):
    assert (await api.get("/settings"))["app"] == {
        "close_to_tray": True, "setup_done": False, "devtools": False, "theme": "system", "text_size": "standard",
        "start_at_login": None}  # None: not the Windows app
    result = await api.call("PUT", "/settings", {"app": {"theme": "dark", "close_to_tray": False}})

    assert {k: result["settings"]["app"][k] for k in ("close_to_tray", "theme")} == {"close_to_tray": False, "theme": "dark"}
    assert result["applies"] == {"app.theme": "now", "app.close_to_tray": "now"}
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "THEME=dark" in env and "CLOSE_TO_TRAY=false" in env


async def test_text_size_applies_now_and_the_page_opens_at_it(page, tmp_path):
    result = await page.call("PUT", "/settings", {"app": {"text_size": "large"}})

    assert result["applies"] == {"app.text_size": "now"} and result["settings"]["app"]["text_size"] == "large"
    assert "TEXT_SIZE=large" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert '<html data-text-size="large" lang="en">' in await _html(page)
    await page.call("PUT", "/settings", {"app": {"text_size": "standard"}})
    assert '<html lang="en">' in await _html(page)
    error = await page.call("PUT", "/settings", {"app": {"text_size": "huge"}}, status=400)
    assert "text_size must be one of standard, large, largest" in error["error"]


async def test_developer_tools_apply_at_the_next_start(api, tmp_path):
    result = await api.call("PUT", "/settings", {"app": {"devtools": True}})

    assert result["applies"] == {"app.devtools": "next start"} and result["settings"]["app"]["devtools"] is True
    assert "DEVTOOLS=true" in (tmp_path / ".env").read_text(encoding="utf-8")
    await api.call("PUT", "/settings", {"app": {"devtools": "yes"}}, status=400)


class LogRecorder:
    def __init__(self) -> None:
        self.lines: list = []

    def __getattr__(self, level: str):
        return lambda event, **fields: self.lines.append((level, event, fields))


async def test_a_screens_error_goes_to_the_log(api, monkeypatch):
    log = LogRecorder()
    monkeypatch.setattr("api.server.log", log)

    assert await api.call("POST", "/ui/error", {
        "kind": "screen", "message": "destroy is not a function", "route": "#/deals",
        "stack": "TypeError: destroy is not a function\n at Setup" + "x" * 9000, "component": "at Setup"}) == {"logged": True}

    [(level, event, fields)] = log.lines
    assert (level, event) == ("error", "ui_error")
    assert fields["kind"] == "screen" and fields["message"] == "destroy is not a function" and fields["route"] == "#/deals"
    assert fields["stack"].startswith("TypeError") and len(fields["stack"]) == 4000  # a long stack is cut, not refused
    await api.call("POST", "/ui/error", ["not", "an", "object"], status=400)
    resp = await api.client.post("/api/v1/ui/error", json={"message": "x"})  # no token: other pages can't write the log
    assert resp.status == 401


async def test_a_folder_that_wont_open_says_why(api, monkeypatch):
    def fails(target):
        raise FileNotFoundError(2, "No such file or directory", "xdg-open")

    monkeypatch.setattr("api.server.open_path", fails)
    error = await api.call("POST", "/app/open", {"what": "logs"}, status=500)
    assert error["error"].startswith("Couldn't open ") and error["error"].endswith(": No such file or directory")


async def test_the_setup_wizard_runs_until_it_is_finished(api, tmp_path):
    assert (await api.get("/status"))["setup_needed"] is True
    await api.call("PUT", "/settings", {"app": {"setup_done": True}})
    assert (await api.get("/status"))["setup_needed"] is False
    assert "SETUP_DONE=true" in (tmp_path / ".env").read_text(encoding="utf-8")


async def test_starting_at_sign_in_uses_the_run_key(api, monkeypatch):
    state = {"on": False}
    monkeypatch.setattr("desktop.windows.autostart_enabled", lambda: state["on"])
    monkeypatch.setattr("desktop.windows.set_autostart", lambda on: state.update(on=on))

    result = await api.call("PUT", "/settings", {"app": {"start_at_login": True}})

    assert state["on"] is True and result["settings"]["app"]["start_at_login"] is True
    assert not (Path.cwd() / ".env").exists()  # Windows keeps it (the Run key), not .env
    monkeypatch.undo()
    error = await api.call("PUT", "/settings", {"app": {"start_at_login": True}}, status=400)
    assert "installed Windows app" in error["error"]


async def test_keys_are_checked_one_at_a_time(api):
    mock_doctor_telegram(api.engine.router)
    checks = (await api.call("POST", "/setup/check", {"what": "telegram"}))["checks"]
    assert [c["name"] for c in checks] == ["Telegram", "Telegram chat", "Telegram commands"]
    assert all(c["level"] == "ok" for c in checks)
    [anthropic] = (await api.call("POST", "/setup/check", {"what": "anthropic"}))["checks"]
    assert anthropic["level"] == "warn" and "no ANTHROPIC_API_KEY" in anthropic["detail"]
    await api.call("POST", "/setup/check", {"what": "fax"}, status=400)


@pytest.mark.parametrize("tag,newer", [("v99.0.0", True), ("v0.1.0", False)])
async def test_checking_for_updates(api, tag, newer):
    api.engine.router.get(RELEASES_API).mock(return_value=httpx.Response(200, json={
        "tag_name": tag, "html_url": "https://github.com/Chip-jpg/CheapTrip/releases/latest",
        "assets": [{"name": f"CheapTrip-Setup-{tag[1:]}.exe", "browser_download_url": "https://example.com/setup.exe"}]}))
    update = await api.get("/app/update")
    assert update["newer"] is newer and update["latest"] == tag[1:] and update["download"] == "https://example.com/setup.exe"


async def test_clearing_the_price_history(api):
    await api.service.run_cycle()
    assert (await api.call("POST", "/data/clear-price-history"))["deleted"] == 4
    assert (await api.get("/destinations?tab=seen"))["destinations"] == []


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
    ({"alerts": {"instant_alerts_per_hour": 0}}, "Instant alerts: must be between 1 and 60"),
    ({"preferences": {"search_window_days": 0}}, "How far ahead: must be between 14 and 365"),
    ({"preferences": {"search_window_days": "abc"}}, "How far ahead: must be a number"),
    ({"preferences": {"minimum_hotel_rating": 42}}, "Minimum hotel rating: must be between 0 and 10"),
    ({"preferences": {"max_trip_budget": -5}}, "Trip budget: must be more than 0"),
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
