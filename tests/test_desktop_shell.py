"""The desktop app's shell (B40): links, one instance, hand-over, the engine thread, the tray."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer
from click.testing import CliRunner

import control
from api.server import create_app
from desktop import app as desktop_app
from desktop.engine_thread import EngineThread
from desktop.instance import InstanceLock, hand_over, read_app_file, remove_app_file, write_app_file
from desktop.links import clean_route, parse_args, route_for_link
from desktop.tray import SEPARATOR, STATE_COLORS, Tray, tooltip, tray_image
from main import cli
from scheduler.engine import Engine
from storage.database import recent_cycles
from tests.test_engine_service import SlowFlights, _aggregator

TOKEN = "t0k3n-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _lock(folder: Path) -> InstanceLock:
    """A lock of its own per test (on Windows the mutex name is global to the session)."""
    return InstanceLock(folder, name=f"Local\\CheapTrip.Test.{uuid.uuid4().hex}")


def _local(engine) -> None:
    """Let requests to the app's own API through the mocked HTTP layer."""
    engine.router.route(host="127.0.0.1").pass_through()


def _wait(condition, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


class RecordingShell:
    def __init__(self) -> None:
        self.shown: List[Optional[str]] = []
        self.quits = 0

    def show(self, route: Optional[str] = None) -> None:
        self.shown.append(route)

    def quit(self) -> None:
        self.quits += 1


# ── Links ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("route,expected", [
    ("", ""), ("/", ""), ("#/deals", "deals"), ("activity", "activity"), ("deals/ab-12_Z", "deals/ab-12_Z"),
    ("deal/abc", "deals/abc"), ("setup/", "setup"),
    ("admin", None), ("deals/a/b", None), ("deals/<script>", None), ("settings/keys", None),
])
def test_only_the_apps_screens_are_routes(route, expected):
    assert clean_route(route) == expected


@pytest.mark.parametrize("link,expected", [
    ("cheaptrip://deal/abc123", "deals/abc123"), ("CheapTrip://deals/abc123/", "deals/abc123"),
    ("cheaptrip://activity", "activity"), ("cheaptrip://", ""), ("cheaptrip://open", ""),
    ("cheaptrip:deal/abc", "deals/abc"), ("https://deal/abc", None), ("cheaptrip://deal/a%2Fb", None),
])
def test_cheaptrip_links(link, expected):
    assert route_for_link(link) == expected


def test_launch_arguments():
    assert parse_args([]).minimized is False and parse_args([]).route is None
    args = parse_args(["--minimized", "cheaptrip://deal/xyz", "--unknown"])
    assert args.minimized and args.route == "deals/xyz"


# ── One instance, and handing over ────────────────────────────────────────────

def test_only_one_engine_at_a_time(tmp_path):
    name = f"Local\\CheapTrip.Test.{uuid.uuid4().hex}"
    first, second = InstanceLock(tmp_path, name), InstanceLock(tmp_path, name)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_the_app_file_is_private_and_only_removed_by_its_writer(tmp_path):
    path = write_app_file(tmp_path, 51234, "secret")
    assert read_app_file(tmp_path) == {"port": 51234, "token": "secret", "pid": os.getpid()}
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600

    path.write_text('{"port": 1, "token": "x", "pid": 1}', encoding="utf-8")  # another process's
    remove_app_file(tmp_path)
    assert path.exists()
    write_app_file(tmp_path, 51234, "secret")
    remove_app_file(tmp_path)
    assert not path.exists()


async def test_a_second_launch_hands_over_to_the_running_app(engine, tmp_path):
    _local(engine)
    shell = RecordingShell()
    service = Engine(None, _aggregator(), telegram_commands=False)
    client = TestClient(TestServer(create_app(service, TOKEN, shell=shell), host="127.0.0.1"))
    await client.start_server()
    try:
        write_app_file(tmp_path, client.port, TOKEN)
        assert await asyncio.to_thread(hand_over, tmp_path, "deals/abc") is True
        assert await asyncio.to_thread(hand_over, tmp_path, None) is True
        assert shell.shown == ["deals/abc", None]

        write_app_file(tmp_path, client.port, "stolen?")
        assert await asyncio.to_thread(hand_over, tmp_path, None, 0) is False
    finally:
        await client.close()
    (tmp_path / "app.json").unlink()
    assert await asyncio.to_thread(hand_over, tmp_path, None, 0) is False  # nothing running


async def test_the_app_routes_show_and_quit(engine):
    shell = RecordingShell()
    client = TestClient(TestServer(create_app(Engine(None, _aggregator()), TOKEN, shell=shell)))
    await client.start_server()
    try:
        resp = await client.post("/api/v1/app/show", json={"route": "#/activity"}, headers=AUTH)
        assert resp.status == 200
        resp = await client.post("/api/v1/app/show", json={"route": "../etc"}, headers=AUTH)
        assert resp.status == 400
        resp = await client.post("/api/v1/app/quit", headers=AUTH)
        assert resp.status == 200
        resp = await client.post("/api/v1/app/quit")
        assert resp.status == 401
    finally:
        await client.close()
    assert shell.shown == ["activity"] and shell.quits == 1

    browser_only = TestClient(TestServer(create_app(Engine(None, _aggregator()), TOKEN)))
    await browser_only.start_server()
    try:
        assert (await browser_only.post("/api/v1/app/quit", headers=AUTH)).status == 405  # no such route
    finally:
        await browser_only.close()


def test_run_refuses_a_second_engine(engine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lock = InstanceLock(tmp_path)
    assert lock.acquire()
    try:
        result = CliRunner().invoke(cli, ["run"])
    finally:
        lock.release()
    assert result.exit_code == 1 and "already running" in result.output


# ── The engine thread and the tray ────────────────────────────────────────────

class AppStub:
    def __init__(self, engine_thread: EngineThread) -> None:
        self.engine_thread = engine_thread
        self.shown: List[Optional[str]] = []
        self.quits = 0

    def show(self, route: Optional[str] = None) -> None:
        self.shown.append(route)

    def quit(self) -> None:
        self.quits += 1


def test_the_tray_drives_the_engine_in_its_thread(engine):
    _local(engine)
    statuses: List[dict] = []
    thread = EngineThread(lambda: Engine(None, _aggregator(SlowFlights(delay=0.3)), telegram_commands=False),
                          on_status=statuses.append)
    server = thread.start()
    tray = Tray(AppStub(thread))
    try:
        with httpx.Client(trust_env=False) as client:
            resp = client.get(f"http://127.0.0.1:{server.port}/api/v1/status", headers={
                "Authorization": f"Bearer {server.token}"})
        assert resp.status_code == 200

        assert _wait(lambda: statuses and statuses[-1]["state"] == "running")
        assert _wait(lambda: not thread.engine.is_searching)  # the search at start
        before = len(thread.call(recent_cycles()))

        tray.update(statuses[-1])
        tray.search_now()
        assert _wait(lambda: len(thread.call(recent_cycles())) == before + 1 and not thread.engine.is_searching)
        assert thread.call(recent_cycles())[0]["trigger"] == "manual"

        tray.pause(hours=1)
        assert _wait(lambda: statuses[-1]["state"] == "paused")
        assert thread.call(control.paused_until()) is not None
        tray.resume()
        assert _wait(lambda: statuses[-1]["state"] == "running")

        tray.open()
        tray.quit()
        assert tray.app.shown == [None] and tray.app.quits == 1
    finally:
        thread.stop()
    assert not thread.engine.is_running


def test_the_tray_menu_follows_the_state():
    tray = Tray(app=None)

    def shown(state: str) -> List[str]:
        tray.status = {"state": state}
        return [e.label for e in tray.entries() if e is not SEPARATOR and e.visible()]

    assert shown("running") == ["Open CheapTrip", "Search now", "Pause alerts", "Quit CheapTrip"]
    assert shown("paused") == ["Open CheapTrip", "Search now", "Resume alerts", "Quit CheapTrip"]
    tray.status = {"state": "searching"}
    [search] = [e for e in tray.entries() if e.label == "Search now"]
    assert search.enabled() is False
    [pause] = [e for e in tray.entries() if e.label == "Pause alerts"]
    assert [e.label for e in pause.submenu] == ["For 1 hour", "For 4 hours", "Until tomorrow morning",
                                                "Until I resume"]


def test_the_tray_icon_and_tooltip_show_the_state():
    for state, colour in STATE_COLORS.items():
        image = tray_image(state)
        assert image.size == (64, 64) and image.getpixel((8, 32))[:3] == colour

    status = {"state": "running", "next_search_at": "2026-10-01T12:30:00", "timezone": "UTC"}
    assert tooltip(status).startswith("CheapTrip · next search ")
    assert tooltip({"state": "searching"}) == "CheapTrip · searching for deals…"
    assert tooltip({"state": "paused", "paused_until": "2036-10-01T08:00:00"}) == \
        "CheapTrip · alerts paused until you resume"
    assert tooltip({"state": "attention"}).startswith("CheapTrip · needs attention")

    tray = Tray(app=None)
    tray.update({"state": "paused"})  # no icon (no desktop here): nothing to draw, no error
    assert tray.state == "paused"


# ── The app ───────────────────────────────────────────────────────────────────

def test_the_app_starts_hands_over_and_quits(engine, tmp_path, monkeypatch):
    """Browser mode (no WebView2 here): start, a second launch shows a screen, Quit from the API."""
    _local(engine)
    opened, boxes = [], []
    monkeypatch.setattr(desktop_app, "webview2_available", lambda: False)
    monkeypatch.setattr(desktop_app, "message_box", lambda title, text: boxes.append(text))
    monkeypatch.setattr(desktop_app.webbrowser, "open", opened.append)
    monkeypatch.setattr(desktop_app.Tray, "start", lambda self: False)
    monkeypatch.setattr(desktop_app.DesktopApp, "_make_engine",
                        lambda self: Engine(None, _aggregator(), telegram_commands=False))
    lock = _lock(tmp_path)
    monkeypatch.setattr(desktop_app, "InstanceLock", lambda folder: lock)

    result: List[int] = []
    first = threading.Thread(target=lambda: result.append(desktop_app.run(["--minimized"], tmp_path)))
    first.start()
    assert _wait(lambda: read_app_file(tmp_path) is not None)
    assert opened == []  # started minimized

    monkeypatch.setattr(desktop_app, "InstanceLock", lambda folder: _Taken())  # a second launch…
    assert desktop_app.run(["cheaptrip://deal/abc"], tmp_path) == 0  # …hands over
    assert _wait(lambda: len(opened) == 1) and opened[0].endswith("#/deals/abc")
    assert desktop_app.run(["--minimized"], tmp_path) == 0 and len(opened) == 1  # sign-in again: nothing

    info = read_app_file(tmp_path)
    with httpx.Client(trust_env=False) as client:
        resp = client.post(f"http://127.0.0.1:{info['port']}/api/v1/app/quit",
                           headers={"Authorization": f"Bearer {info['token']}"})
    assert resp.status_code == 200
    first.join(timeout=30)
    assert not first.is_alive() and result == [0]
    assert read_app_file(tmp_path) is None
    assert boxes == []  # started at sign-in: no "opened in your browser" message


class _Taken:
    def acquire(self) -> bool:
        return False

    def release(self) -> None:
        pass


def test_notification_buttons_in_the_app(engine, tmp_path):
    app = desktop_app.DesktopApp(tmp_path)
    shown: List[Any] = []
    app.show = shown.append
    assert app.notification_action("details", "abc") is None
    assert app.notification_action("show", "activity") is None
    mute = app.notification_action("mute", "KRK")
    assert asyncio.iscoroutine(mute)
    mute.close()
    assert shown == ["deals/abc", "activity"]
