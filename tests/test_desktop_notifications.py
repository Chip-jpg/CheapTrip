"""Desktop notifications and the alert channels (B39)."""
from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any, List

import aiosqlite
import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer
from desktop_notifier import DEFAULT_SOUND

import control
from api.server import create_app
from api.settings_io import apply_settings, read_settings
from config import get_settings
from notifier import telegram as telegram_module
from notifier.channels import ChannelNotifier
from notifier.desktop import DesktopNotifier, deal_toast, digest_toast
from preferences import get_preferences
from scheduler import engine as engine_service
from scheduler.engine import Engine
from storage.database import get_db_path, get_pending_instant_alerts, init_db
from storage.models import DealType, FlightLeg, HotelDeal, RepositioningLeg, Trip
from tests.harness import flight
from tests.test_engine_service import _aggregator
from tests.test_health_monitor import _blocked

DEP, RET = date(2026, 10, 21), date(2026, 10, 28)  # a Wednesday
TOKEN = "t0k3n-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeBackend:
    """Stands in for desktop_notifier.DesktopNotifier: records what would be shown."""

    def __init__(self, fail: bool = False, refuse: bool = False) -> None:
        self.fail, self.refuse = fail, refuse
        self.sent: List[dict] = []

    async def send(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("no notification service")
        if self.refuse:  # what desktop-notifier does when the system refuses: log, return normally
            return
        self.sent.append(kwargs)
        kwargs["on_dispatched"]()

    @property
    def titles(self) -> List[str]:
        return [n["title"] for n in self.sent]


@pytest.fixture(autouse=True)
def _no_telegram_backoff(monkeypatch):
    async def _instant(seconds: float) -> None:
        return None

    monkeypatch.setattr(telegram_module, "_pause", _instant)


def _setting(monkeypatch, **values: Any) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name.upper(), str(value).lower())
    get_settings.cache_clear()


def _trip(**changes: Any) -> Trip:
    leg = FlightLeg(origin="MXP", destination="BER", price_eur=30.0, departure_date=DEP, return_date=RET,
                    airline="Ryanair", booking_url="https://example.com/book", source="ryanair")
    fields = dict(deal_type=DealType.FLIGHT_ONLY, route="Milan → Berlin", outbound_flight=leg,
                  total_cost_eur=30.0, normal_price_eur=56.0, discount_pct=46.4, departure_date=DEP,
                  return_date=RET, nights=7, source_list=["ryanair"], hash="deal-1")
    fields.update(changes)
    return Trip(**fields)


def _desktop(fail: bool = False, **kwargs: Any):
    backend = FakeBackend(fail)
    return DesktopNotifier(backend, **kwargs), backend


async def _channels(desktop: Any = None) -> ChannelNotifier:
    await init_db()
    return await ChannelNotifier.create(desktop)


async def _alert_channels() -> List[tuple]:
    async with aiosqlite.connect(await get_db_path()) as db:
        return await (await db.execute("SELECT alert_tier, channel FROM alerts_sent ORDER BY id")).fetchall()


# ── What a notification says ──────────────────────────────────────────────────

def test_a_deal_says_what_how_much_why_and_when():
    toast = deal_toast(_trip())

    assert toast.title == "Milan → Berlin · €30"
    assert toast.message == "46% below the usual €56 · Wed 21–28 Oct · Ryanair"
    assert [label for label, _ in toast.buttons] == ["Book €30", "Details", "Mute Berlin"]
    assert [action for _, action in toast.buttons] == [
        ("open", "https://example.com/book"), ("details", "deal-1"), ("mute", "BER")]
    assert toast.click == ("details", "deal-1")


def test_a_price_drop_and_an_error_fare():
    drop = deal_toast(_trip(previous_price_eur=45.0))
    assert drop.message.startswith("Now €30, was €45 · ")

    error = deal_toast(_trip(is_error_fare=True))
    assert error.title == "⚠ Possible error fare: Milan → Berlin · €30"
    assert error.message.startswith("Book fast, verify after · ")
    assert error.buttons[0][0] == "Grab €30"


def test_a_package_a_hotel_a_trip_with_hotel_and_a_route_via_a_hub():
    package_leg = FlightLeg(origin="MXP", destination="BER", price_eur=199.0, departure_date=DEP, return_date=RET,
                            is_package=True, package_hotel="Hotel Adlon", source="holiday_pirates")
    package = deal_toast(_trip(deal_type=DealType.PACKAGE, outbound_flight=package_leg, total_cost_eur=199.0,
                               nights=3, discount_pct=None, normal_price_eur=None))
    assert package.title == "Berlin package · €199 per person"
    assert package.message == "Flight + 3 nights, Hotel Adlon · Wed 21–28 Oct"
    package_leg.booking_url = "https://example.com/package"
    assert deal_toast(_trip(deal_type=DealType.PACKAGE, outbound_flight=package_leg)).buttons[0][0] == "View package"

    stay = HotelDeal(name="Hotel Test", location="Krakow, Poland", price_per_night_eur=19.5, nights=3,
                     total_price_eur=58.5, rating=8.6, check_in=DEP, check_out=date(2026, 10, 24),
                     booking_url="https://example.com/hotel")
    hotel = deal_toast(Trip(deal_type=DealType.HOTEL_ONLY, route="Hotel Test, Krakow", hotel=stay,
                            total_cost_eur=58.5, departure_date=DEP, return_date=date(2026, 10, 24), hash="h"))
    assert hotel.title == "Hotel Test, Krakow · €19.50/night"
    assert hotel.message == "8.6/10 · Wed 21–24 Oct"
    assert [label for label, _ in hotel.buttons] == ["Reserve", "Details", "Mute Krakow"]

    with_hotel = deal_toast(_trip(deal_type=DealType.COMPLETE_TRIP, hotel=stay, total_cost_eur=88.5))
    assert with_hotel.title == "Milan → Berlin · €88 with hotel"
    assert "7 nights at Hotel Test" in with_hotel.message

    via = RepositioningLeg(origin="MXP", hub="BLQ", transport_type="train", price_eur=20.0, duration_hours=2.0)
    long_haul = FlightLeg(origin="BLQ", destination="JFK", price_eur=280.0, departure_date=DEP, return_date=RET)
    repositioned = deal_toast(_trip(deal_type=DealType.REPOSITIONED, outbound_flight=long_haul,
                                    repositioning_legs=[via], total_cost_eur=300.0))
    assert repositioned.title == "Milan → Bologna → New York · €300"
    assert repositioned.message.startswith("Via Bologna · ")


def test_the_digest_names_the_best_deals():
    trips = [_trip(total_cost_eur=price, hash=str(price)) for price in (60.0, 30.0, 45.0)]
    toast = digest_toast(trips)
    assert toast.title == "Daily digest: 3 deals from €30"
    assert toast.message == "Berlin €30, Berlin €45, Berlin €60"
    assert toast.buttons == [("Open deals", ("show", "deals"))] and toast.click == ("show", "deals")
    more = digest_toast([_trip(total_cost_eur=float(p), hash=str(p)) for p in range(30, 80, 10)])
    assert more.message == "Berlin €30, Berlin €40, Berlin €50 and 2 more"


# ── Showing it ────────────────────────────────────────────────────────────────

async def test_buttons_open_the_link_or_hand_over_to_the_app():
    opened, actions = [], []
    desktop, backend = _desktop(open_url=opened.append, on_action=lambda kind, value: actions.append((kind, value)))

    assert await desktop.send_deal(_trip()) is True
    [shown] = backend.sent
    assert [b.title for b in shown["buttons"]] == ["Book €30", "Details", "Mute Berlin"]
    for button in shown["buttons"]:
        button.on_pressed()
    shown["on_clicked"]()

    assert opened == ["https://example.com/book"]
    assert actions == [("details", "deal-1"), ("mute", "BER"), ("details", "deal-1")]
    assert shown["sound"] == DEFAULT_SOUND


async def test_without_the_app_only_the_booking_link_is_offered():
    desktop, backend = _desktop()
    await desktop.send_deal(_trip())
    [shown] = backend.sent
    assert [b.title for b in shown["buttons"]] == ["Book €30"] and shown["on_clicked"] is None


async def test_mute_from_a_notification_mutes_at_once(engine):
    await init_db()
    desktop, backend = _desktop(on_action=lambda kind, value: control.set_muted(value, True))
    await desktop.send_deal(_trip())

    backend.sent[0]["buttons"][2].on_pressed()  # "Mute Berlin": a coroutine, run on the event loop
    for _ in range(100):  # it runs in the background: wait for it (up to 5 s on a slow machine)
        if "BER" in get_preferences().excluded_destinations:
            break
        await asyncio.sleep(0.05)

    assert "BER" in get_preferences().excluded_destinations


async def test_sound_can_be_switched_off(engine, monkeypatch):
    _setting(monkeypatch, notify_sound=False)
    desktop, backend = _desktop()
    await desktop.send_test()
    assert backend.sent[0]["sound"] is None


async def test_a_machine_without_notifications_is_not_an_error():
    desktop, _ = _desktop(fail=True)
    assert await desktop.send_deal(_trip()) is False
    assert desktop.failures == 1


async def test_a_notification_the_system_refused_is_not_counted_as_shown():
    refusing = DesktopNotifier(FakeBackend(refuse=True))
    assert await refusing.send_deal(_trip()) is False
    assert refusing.failures == 1


async def test_the_real_library_reports_a_refusal():
    """No notification service here (CI, containers): desktop-notifier logs it; we see False."""
    desktop = DesktopNotifier()
    if desktop._get_backend()._backend.__class__.__name__ != "DBusDesktopNotifier" or os.environ.get(
            "DBUS_SESSION_BUS_ADDRESS"):
        pytest.skip("needs a Linux machine without a notification service")
    assert await desktop.send_test() is False


# ── Channels ──────────────────────────────────────────────────────────────────

async def test_an_instant_deal_goes_to_the_desktop_and_telegram(engine):
    engine.set_prefs(priority_destinations=["KRK"])
    desktop, backend = _desktop()

    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)], notifier=await _channels(desktop))

    assert backend.titles == ["Milan → Krakow · €300"]
    assert len(engine.telegram.texts) == 1 and "Krakow" in engine.telegram.texts[0]
    assert await _alert_channels() == [("instant", "desktop,telegram")]


@pytest.mark.parametrize("off,left", [("notify_telegram", "desktop"), ("notify_desktop", "telegram")])
async def test_each_channel_can_be_switched_off(engine, monkeypatch, off, left):
    engine.set_prefs(priority_destinations=["KRK"])
    _setting(monkeypatch, **{off: False})
    desktop, backend = _desktop()

    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)], notifier=await _channels(desktop))

    assert len(backend.sent) == (left == "desktop")
    assert len(engine.telegram.texts) == (left == "telegram")
    assert await _alert_channels() == [("instant", left)]


async def test_without_a_bot_deals_only_reach_the_desktop(engine, monkeypatch):
    engine.set_prefs(priority_destinations=["KRK"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    get_settings.cache_clear()
    desktop, backend = _desktop()
    notifier = await _channels(desktop)

    assert notifier.channels("instant") == ["desktop"] and notifier.channels("system") == []
    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)], notifier=notifier)
    assert len(backend.sent) == 1 and engine.telegram.texts == []


async def test_instant_alerts_switched_off_wait_for_the_digest(engine, monkeypatch):
    engine.set_prefs(priority_destinations=["KRK"])
    _setting(monkeypatch, notify_instant=False)
    desktop, backend = _desktop()

    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)], notifier=await _channels(desktop))

    assert backend.sent == [] and engine.telegram.texts == []
    assert len(await get_pending_instant_alerts()) == 1


async def test_the_hourly_limit_is_shared_by_both_channels(engine, monkeypatch):
    engine.set_prefs(priority_destinations=["KRK", "PRG", "BCN"])
    _setting(monkeypatch, instant_alerts_per_hour=2)
    desktop, backend = _desktop()
    fares = [flight(destination=code, price=300.0) for code in ("KRK", "PRG", "BCN")]

    await engine.run_cycle(flights=fares, notifier=await _channels(desktop))

    assert len(backend.sent) == 2 and len(engine.telegram.texts) == 2
    assert await _alert_channels() == [("instant", "desktop,telegram")] * 2
    assert len(await get_pending_instant_alerts()) == 1


async def test_when_the_desktop_fails_telegram_still_gets_the_deal(engine):
    engine.set_prefs(priority_destinations=["KRK"])
    desktop, _ = _desktop(fail=True)

    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)], notifier=await _channels(desktop))

    assert len(engine.telegram.texts) == 1
    assert await _alert_channels() == [("instant", "telegram")]


async def test_when_every_channel_fails_the_deal_waits_and_keeps_its_slot(engine):
    engine.set_prefs(priority_destinations=["KRK"])
    engine.telegram.queue_responses(*[httpx.Response(500, text="down")] * 3)
    desktop, _ = _desktop(fail=True)
    notifier = await _channels(desktop)

    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)], notifier=notifier)

    assert await _alert_channels() == []
    assert len(await get_pending_instant_alerts()) == 1
    assert await notifier.rate_limiter.instant_remaining() == get_settings().instant_alerts_per_hour


async def test_the_digest_goes_to_both_channels(engine):
    desktop, backend = _desktop()
    notifier = await _channels(desktop)
    await engine.run_cycle(flights=[flight(destination="PRG", price=40.0), flight(destination="BCN", price=55.0)],
                           notifier=notifier)
    engine.telegram.requests.clear()

    await engine.run_digest(notifier)

    assert backend.titles[-1] == "Daily digest: 2 deals from €40"
    assert backend.sent[-1]["message"] == "Prague €40, Barcelona €55"
    assert len(engine.telegram.texts) == 1
    assert {row for row in await _alert_channels()} == {("digest", "desktop,telegram")}


async def test_a_failing_source_is_a_desktop_notice_too(engine):
    desktop, backend = _desktop()
    notifier = await _channels(desktop)
    for _ in range(4):
        await engine.run_cycle(flights=_blocked, notifier=notifier)
    await engine.run_cycle(flights=[flight(price=300.0)], notifier=notifier)

    assert backend.titles == ["Fake Flights isn't working", "Fake Flights works again"]
    assert backend.sent[0]["on_clicked"] is None  # no app to show Activity in
    assert sum("Source failing" in t for t in engine.telegram.texts) == 1


async def test_source_notices_can_be_switched_off(engine, monkeypatch):
    _setting(monkeypatch, notify_source_problems=False)
    desktop, backend = _desktop()
    notifier = await _channels(desktop)
    for _ in range(4):
        await engine.run_cycle(flights=_blocked, notifier=notifier)
    await engine.run_cycle(flights=[flight(price=300.0)], notifier=notifier)

    assert backend.sent == []
    assert not any("Source" in t for t in engine.telegram.texts)


async def test_status_messages_stay_on_telegram(engine):
    desktop, backend = _desktop()
    notifier = await _channels(desktop)
    assert await notifier.send_system_message("🚀 <b>Engine started</b>") is True
    assert backend.sent == [] and len(engine.telegram.texts) == 1


# ── The app ───────────────────────────────────────────────────────────────────

async def test_the_app_sends_a_test_notification(engine, monkeypatch):
    await init_db()
    desktop, backend = _desktop()
    service = Engine(await ChannelNotifier.create(desktop), _aggregator(), telegram_commands=False)
    await service.start(first_search=False)
    client = TestClient(TestServer(create_app(service, TOKEN)))
    await client.start_server()
    url = "/api/v1/setup/test-notification"
    try:
        resp = await client.post(url, headers=AUTH)
        assert resp.status == 200 and await resp.json() == {"sent": {"desktop": True, "telegram": True}}
        assert backend.titles == ["CheapTrip notifications work"]

        resp = await client.post(url, json={"channel": "desktop"}, headers=AUTH)
        assert await resp.json() == {"sent": {"desktop": True}}

        resp = await client.post(url, json={"channel": "fax"}, headers=AUTH)
        assert resp.status == 400

        _setting(monkeypatch, notify_desktop=False, notify_telegram=False)
        resp = await client.post(url, headers=AUTH)
        assert resp.status == 409
    finally:
        await client.close()
        await service.stop()


async def test_notification_settings_apply_at_once(engine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    notifier = await _channels(_desktop()[0])
    assert notifier.channels("instant") == ["desktop", "telegram"]

    result = await apply_settings({"notifications": {"desktop": False, "sound": False}})

    assert result["applies"] == {"notifications.desktop": "now", "notifications.sound": "now"}
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "NOTIFY_DESKTOP=false" in env and "NOTIFY_SOUND=false" in env
    assert notifier.channels("instant") == ["telegram"]
    shown = read_settings()["notifications"]
    assert shown["desktop"] is False and shown["telegram"] is True and shown["telegram_ready"] is True

    with pytest.raises(ValueError):
        await apply_settings({"notifications": {"carrier_pigeon": True}})


async def test_command_replies_use_telegram_even_with_alerts_there_off(engine, monkeypatch):
    bots = []

    class RecordingBot:
        def __init__(self, notifier: Any) -> None:
            bots.append(notifier)

        async def run(self, stop: asyncio.Event) -> None:
            await stop.wait()

    monkeypatch.setattr(engine_service, "CommandBot", RecordingBot)
    _setting(monkeypatch, notify_telegram=False)
    await init_db()
    notifier = await ChannelNotifier.create(_desktop()[0])
    service = Engine(notifier, _aggregator())
    await service.start(first_search=False)
    await service.stop()

    assert bots == [notifier.telegram]


async def test_the_notifications_screen_previews_and_sends(engine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    await init_db()
    desktop, backend = _desktop(on_action=lambda kind, value: None, image_folder=tmp_path / "data" / "toasts")
    notifier = await ChannelNotifier.create(desktop)
    await engine.run_cycle(flights=[flight(destination="PRG", price=40.0), flight(destination="BCN", price=55.0)],
                           notifier=notifier)
    service = Engine(notifier, _aggregator(), telegram_commands=False)
    await service.start(first_search=False)
    client = TestClient(TestServer(create_app(service, TOKEN)))
    await client.start_server()
    try:
        answer = await (await client.get("/api/v1/notifications/previews", headers=AUTH)).json()
        previews = {p["kind"]: p for p in answer["previews"]}
        assert list(previews) == ["deal", "digest", "notice", "test"]  # no error fare today
        deal = previews["deal"]
        assert deal["title"] == "Milan → Prague · €40" and deal["buttons"][0] == "Book €40"
        assert previews["digest"]["title"] == "Daily digest: 2 deals from €40"
        assert previews["notice"]["example"] is True  # every source works: an example of what a problem looks like
        assert answer["desktop"] == {"available": True, "on": True, "sound": True}

        picture = await client.get(deal["image"], headers=AUTH)  # the deal's picture, as the toast shows it
        assert picture.status == 200 and picture.content_type == "image/png"
        assert (await client.get("/api/v1/notifications/image/..%2F..%2F.env", headers=AUTH)).status == 404

        url = "/api/v1/setup/test-notification"
        resp = await client.post(url, json={"channel": "desktop", "kind": "digest"}, headers=AUTH)
        assert await resp.json() == {"sent": {"desktop": True}}
        assert backend.titles[-1] == "Daily digest: 2 deals from €40"
        resp = await client.post(url, json={"kind": "deal"}, headers=AUTH)
        assert backend.sent[-1]["attachment"] is not None  # the deal's picture goes with it
        assert (await client.post(url, json={"kind": "error_fare"}, headers=AUTH)).status == 400
    finally:
        await client.close()
        await service.stop()


def test_a_deal_picture_shows_the_price_and_its_fare_range(tmp_path):
    from PIL import Image

    from notifier.toast_image import SIZE, render

    comparison = {"median": 56.0, "points": [
        {"depart": "2026-10-11", "price": 58.0, "is_this": False}, {"depart": "2026-10-21", "price": 30.0, "is_this": True},
        {"depart": "2026-11-01", "price": 55.0, "is_this": False}]}
    path = render(_trip(), comparison, tmp_path)
    with Image.open(path) as image:
        assert image.size == SIZE and path.name == "deal-1.png"
    assert render(_trip(outbound_flight=None, hotel=None), None, tmp_path) is None  # nothing to draw
