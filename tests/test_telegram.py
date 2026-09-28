"""Telegram delivery: HTML escaping, retries, slot release, pending queue, AI polish."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import aiosqlite
import httpx
import pytest

import ai_layer.formatter as formatter
from notifier.telegram import TelegramNotifier
from storage.database import get_db_path, init_db, save_deal
from storage.models import AlertTier, BookingConfidence, DealType, FlightLeg, HotelDeal, Trip
from tests.harness import flight


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """Record backoff sleeps instead of waiting."""
    sleeps = []

    async def fake_pause(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("notifier.telegram._pause", fake_pause)
    return sleeps


def _trip(route="Milan → Krakow", cost=45.0, hotel_name=None, airline="Ryanair") -> Trip:
    leg = FlightLeg(
        origin="MXP", destination="KRK", price_eur=cost, departure_date=date(2026, 11, 6),
        return_date=date(2026, 11, 9), airline=airline, booking_url="https://example.com/b?x=1&y=2",
        source="test", data_confidence_score=0.8,
    )
    trip = Trip(
        deal_type=DealType.COMPLETE_TRIP if hotel_name else DealType.FLIGHT_ONLY,
        route=route, outbound_flight=leg, flight_cost_eur=cost, total_cost_eur=cost,
        departure_date=leg.departure_date, return_date=leg.return_date,
        booking_confidence=BookingConfidence.MEDIUM, data_confidence_score=0.8, verdict="BOOK NOW",
    )
    if hotel_name:
        trip.hotel = HotelDeal(
            name=hotel_name, location="Krakow", price_per_night_eur=30.0, nights=3, total_price_eur=90.0,
        )
    trip.hash = trip.compute_hash()
    return trip


async def _notifier() -> TelegramNotifier:
    await init_db()
    return await TelegramNotifier.create()


# ── Formatting ───────────────────────────────────────────────────────────────

async def test_messages_are_html_with_escaped_values(engine):
    notifier = await _notifier()
    trip = _trip(hotel_name="Hotel <Best> & Co_*[x]", airline="Air *Star*_")
    await save_deal(trip)
    assert await notifier.send_instant_alert(trip)

    payload = engine.telegram.payloads[0]
    assert payload["parse_mode"] == "HTML"
    text = payload["text"]
    assert "Hotel &lt;Best&gt; &amp; Co_*[x]" in text
    assert "<Best>" not in text
    assert 'href="https://example.com/b?x=1&amp;y=2"' in text
    assert formatter._tags_are_valid(text)


# ── Retries and failures ─────────────────────────────────────────────────────

async def test_429_is_retried_after_retry_after_and_uses_one_slot(engine, no_backoff):
    engine.telegram.queue_responses(
        httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 7}}),
    )
    notifier = await _notifier()
    trip = _trip()
    await save_deal(trip)

    assert await notifier.send_instant_alert(trip)
    assert len(engine.telegram.requests) == 2
    assert 7 in no_backoff
    assert await notifier._rate_limiter.instant_remaining() == 4


async def test_retry_after_is_capped(engine, no_backoff):
    engine.telegram.queue_responses(httpx.Response(429, json={"parameters": {"retry_after": 3600}}))
    notifier = await _notifier()
    assert await notifier.send_system_message("hello")
    assert max(no_backoff) == 60


async def test_parse_error_falls_back_to_plain_text(engine):
    engine.telegram.queue_responses(
        httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"}),
    )
    notifier = await _notifier()
    assert await notifier.send_system_message("<b>Cycle</b> &amp; summary")

    first, second = engine.telegram.payloads
    assert first["parse_mode"] == "HTML"
    assert "parse_mode" not in second
    assert second["text"] == "ℹ️ Cycle & summary"


async def test_network_errors_give_up_without_raising_and_release_slot(engine):
    engine.telegram.queue_responses(*[httpx.ConnectError("boom")] * 3)
    notifier = await _notifier()
    trip = _trip()
    await save_deal(trip)

    assert await notifier.send_instant_alert(trip) is False
    assert len(engine.telegram.requests) == 3
    assert await notifier._rate_limiter.instant_remaining() == 5


async def test_client_error_is_not_retried(engine):
    engine.telegram.queue_responses(httpx.Response(400, json={"description": "Bad Request: chat not found"}))
    notifier = await _notifier()
    assert await notifier.send_system_message("hi") is False
    assert len(engine.telegram.requests) == 1


async def test_failed_digest_releases_daily_slot(engine):
    engine.telegram.queue_responses(*[httpx.Response(502)] * 3)
    notifier = await _notifier()
    trip = _trip()
    await save_deal(trip)

    assert await notifier.send_digest([trip]) is False
    assert await notifier.send_digest([trip]) is True


async def test_over_long_message_is_cut_at_a_line_boundary(engine):
    notifier = await _notifier()
    long_text = "\n".join(f"<b>line {i}</b>" for i in range(1000))
    assert await notifier.send_system_message(long_text)
    sent = engine.telegram.texts[0]
    assert len(sent) <= 4096
    assert formatter._tags_are_valid(sent)


# ── Pending instant queue ────────────────────────────────────────────────────

async def _age_alerts(hours: float) -> None:
    async with aiosqlite.connect(await get_db_path()) as db:
        old = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        await db.execute("UPDATE alerts_sent SET sent_at = ?", (old,))
        await db.execute("UPDATE deals SET created_at = ? WHERE is_alerted = 1", (old,))
        await db.commit()


async def test_rate_limited_deal_is_sent_next_cycle(engine, monkeypatch):
    monkeypatch.setenv("INSTANT_ALERTS_PER_HOUR", "1")
    from config import get_settings
    get_settings.cache_clear()

    await engine.run_cycle(flights=[flight(destination="KRK", price=25.0), flight(destination="PRG", price=30.0)])
    assert len(engine.telegram.texts) == 1

    await _age_alerts(hours=2)  # the hourly quota frees up
    await engine.run_cycle(flights=[])

    assert len(engine.telegram.texts) == 2
    routes = {"Milan → Krakow", "Milan → Prague"}
    assert all(any(r in t for t in engine.telegram.texts) for r in routes)


async def test_stale_pending_instant_moves_to_digest(engine, monkeypatch):
    monkeypatch.setenv("INSTANT_ALERTS_PER_HOUR", "0")
    from config import get_settings
    get_settings.cache_clear()

    await engine.run_cycle(flights=[flight(destination="KRK", price=25.0)])
    assert engine.telegram.texts == []

    async with aiosqlite.connect(await get_db_path()) as db:
        old = (datetime.utcnow() - timedelta(hours=7)).isoformat()
        await db.execute("UPDATE deals SET created_at = ?", (old,))
        await db.commit()

    monkeypatch.setenv("INSTANT_ALERTS_PER_HOUR", "5")
    get_settings.cache_clear()
    await engine.run_cycle(flights=[])

    assert engine.telegram.texts == []
    async with aiosqlite.connect(await get_db_path()) as db:
        cur = await db.execute("SELECT alert_tier, json_extract(payload, '$.alert_tier') FROM deals")
        assert await cur.fetchall() == [(AlertTier.DIGEST.value, AlertTier.DIGEST.value)]


# ── AI polish ────────────────────────────────────────────────────────────────

class _FakeMessages:
    def __init__(self, reply=None, error=None, stop_reason="end_turn"):
        self.reply, self.error, self.stop_reason, self.calls = reply, error, stop_reason, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=self.reply)],
        )


def _use_fake_ai(monkeypatch, fake):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(formatter, "_make_client", lambda key, timeout: SimpleNamespace(messages=fake))


BASE = '✈️ <b>FLIGHT DEAL</b>\n<b>Price:</b> €45\n<code>MXP → KRK</code>\n<a href="https://x.test/b">🔗 Book Now</a>'


async def test_ai_polish_is_used_when_it_preserves_facts(engine, monkeypatch):
    polished = '✈️✨ <b>Flight deal!</b>\n💰 <b>Price:</b> €45\n<code>MXP → KRK</code>\n<a href="https://x.test/b">🔗 Book</a>'
    fake = _FakeMessages(reply=polished)
    _use_fake_ai(monkeypatch, fake)
    assert await formatter.enhance_with_ai(_trip(), BASE) == polished
    assert fake.calls[0]["model"] == "claude-haiku-4-5"


@pytest.mark.parametrize("bad", [
    '✈️ <b>FLIGHT DEAL</b>\n<b>Price:</b> €39\n<code>MXP → KRK</code>\n<a href="https://x.test/b">Book</a>',
    '✈️ <b>FLIGHT DEAL</b>\n<b>Price:</b> €45\n<code>MXP → WAW</code>\n<a href="https://x.test/b">Book</a>',
    '✈️ <b>FLIGHT DEAL</b>\n<b>Price:</b> €45\n<code>MXP → KRK</code>\n<a href="https://evil.test">Book</a>',
    '✈️ <b>FLIGHT DEAL\n<b>Price:</b> €45\n<code>MXP → KRK</code>\n<a href="https://x.test/b">Book</a>',
    '✈️ <h1>FLIGHT DEAL</h1>\n<b>Price:</b> €45\n<code>MXP → KRK</code>\n<a href="https://x.test/b">Book</a>',
    '',
])
async def test_ai_polish_rejected_when_it_changes_facts_or_markup(engine, monkeypatch, bad):
    _use_fake_ai(monkeypatch, _FakeMessages(reply=bad))
    assert await formatter.enhance_with_ai(_trip(), BASE) == BASE


async def test_ai_errors_and_refusals_fall_back(engine, monkeypatch):
    _use_fake_ai(monkeypatch, _FakeMessages(error=RuntimeError("timeout")))
    assert await formatter.enhance_with_ai(_trip(), BASE) == BASE
    _use_fake_ai(monkeypatch, _FakeMessages(reply="nope", stop_reason="refusal"))
    assert await formatter.enhance_with_ai(_trip(), BASE) == BASE


async def test_no_api_key_skips_ai(engine, monkeypatch):
    called = []
    monkeypatch.setattr(formatter, "_make_client", lambda *a: called.append(a))
    assert await formatter.enhance_with_ai(_trip(), BASE) == BASE
    assert called == []
