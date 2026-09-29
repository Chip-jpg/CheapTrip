"""First run with real keys (B30): doctor's live checks, test-alert, read-feed."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import anthropic
import httpx
import httpx2
import pytest
import respx
from click.testing import CliRunner

from main import cli
from scrapers import piratinviaggio
from scrapers.piratinviaggio import PiratinViaggioScraper
from storage.database import get_db_path, init_db
from tests.harness import flight, mock_doctor_telegram, reset_caches
from tests.test_hotel_only_deals import HOTEL_HTML, HOTEL_URL
from tests.test_hotel_only_deals import _option as hotel_option
from tests.test_piratinviaggio import FLIGHT_OPTION, FLIGHTS_URL, PARIS_URL, FakeClaude, _mock_site, _option
from utils.doctor import run_checks, send_test_message


def _lines(checks):
    return {c.name: (c.level, c.detail) for c in checks}


# ── doctor: Telegram ──────────────────────────────────────────────────────────

async def test_doctor_checks_the_chat_and_the_webhook(engine):
    mock_doctor_telegram(engine.router)

    checks = _lines(await run_checks())

    assert checks["Telegram chat"] == ("ok", "the bot can write to Sam")
    assert checks["Telegram commands"][0] == "ok"


async def test_doctor_fails_when_the_bot_cannot_reach_the_chat(engine):
    not_found = httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
    mock_doctor_telegram(engine.router, chat=not_found)

    level, detail = _lines(await run_checks())["Telegram chat"]

    assert level == "fail"
    assert detail.startswith("Bad Request: chat not found: open the bot in Telegram and press Start")


async def test_doctor_warns_that_a_webhook_blocks_commands(engine):
    mock_doctor_telegram(engine.router, webhook="https://example.com/hook")

    level, detail = _lines(await run_checks())["Telegram commands"]

    assert level == "warn" and "deleteWebhook" in detail
    assert "123456:TEST-TOKEN" not in detail  # never print the token


# ── doctor: Anthropic ─────────────────────────────────────────────────────────

class FakeModels:
    def __init__(self, answer) -> None:
        self.answer = answer
        self.asked = []

    async def retrieve(self, model_id: str):
        self.asked.append(model_id)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _api_error(cls, status: int):
    request = httpx2.Request("GET", "https://api.anthropic.com/v1/models/claude-haiku-4-5")
    return cls("error", response=httpx2.Response(status, request=request), body=None)


def _anthropic(monkeypatch, answer) -> FakeModels:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    reset_caches()
    models = FakeModels(answer)

    async def _close():
        return None

    client = SimpleNamespace(models=models, close=_close)
    monkeypatch.setattr("ai_layer.formatter._make_client", lambda api_key, timeout: client)
    return models


@pytest.mark.parametrize("answer,level,text", [
    (SimpleNamespace(display_name="Claude Haiku 4.5"), "ok", "key works with Claude Haiku 4.5"),
    (_api_error(anthropic.AuthenticationError, 401), "fail", "ANTHROPIC_API_KEY was rejected"),
    (_api_error(anthropic.NotFoundError, 404), "fail", "is not a model this key can use"),
    (_api_error(anthropic.InternalServerError, 500), "warn", "could not check the key (HTTP 500)"),
])
async def test_doctor_checks_the_anthropic_key_without_spending_tokens(engine, monkeypatch, answer, level, text):
    mock_doctor_telegram(engine.router)
    models = _anthropic(monkeypatch, answer)

    checks = _lines(await run_checks())

    assert checks["Anthropic"][0] == level and text in checks["Anthropic"][1]
    assert models.asked == ["claude-haiku-4-5"]  # a model lookup, not a message


# ── test-alert ────────────────────────────────────────────────────────────────

async def test_test_alert_sends_the_top_deals(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=40.0)])
    before = len(engine.telegram.texts)

    sent, detail = await send_test_message()

    assert sent and "check your Telegram chat" in detail
    [message] = engine.telegram.texts[before:]
    assert "Test message from CheapTrip" in message
    assert "Top deals, last 24h" in message and "Krakow" in message


async def test_test_alert_with_no_deals_yet(engine):
    sent, _ = await send_test_message()

    assert sent
    assert "No deals in the last 24 hours yet." in engine.telegram.texts[-1]


def test_test_alert_command_needs_telegram_settings(engine, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    reset_caches()

    result = CliRunner().invoke(cli, ["test-alert"])

    assert result.exit_code == 1 and "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are needed" in result.output
    assert engine.telegram.texts == []


# ── read-feed ─────────────────────────────────────────────────────────────────

@pytest.fixture
def claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    reset_caches()
    monkeypatch.setattr(piratinviaggio, "_make_client", lambda api_key, timeout: fake)
    monkeypatch.setattr(piratinviaggio, "_now", lambda: datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc))
    return fake


async def _cached_rows() -> int:
    async with aiosqlite.connect(await get_db_path()) as db:
        return (await (await db.execute("SELECT COUNT(*) FROM feed_extractions")).fetchone())[0]


async def test_preview_reads_the_newest_posts_and_saves_nothing(engine, claude):
    await init_db()
    claude.answers = [FLIGHT_OPTION, _option(price_eur=999.0)]
    with respx.mock(assert_all_called=False) as router:
        _mock_site(router)
        results = await PiratinViaggioScraper().preview(5)

    [(flights, kept), (paris, rejected)] = results
    assert (flights.url, kept.outcome, kept.option.destination) == (FLIGHTS_URL, "deal", "MAD")
    assert (paris.url, rejected.outcome) == (PARIS_URL, "rejected: price €999 is not written in the post")
    assert await _cached_rows() == 0


async def test_preview_is_limited_to_the_newest_posts(engine, claude):
    claude.answers = [FLIGHT_OPTION]
    with respx.mock(assert_all_called=False) as router:
        _mock_site(router)
        results = await PiratinViaggioScraper().preview(1)

    assert [item.url for item, _ in results] == [FLIGHTS_URL]


async def test_cycles_still_cache_what_they_read(engine, claude):
    await init_db()
    claude.answers = [FLIGHT_OPTION, _option()]
    with respx.mock(assert_all_called=False) as router:
        _mock_site(router)
        await PiratinViaggioScraper().safe_scrape([])

    assert await _cached_rows() == 2


def test_read_feed_command_prints_each_post(engine, claude):
    claude.answers = [FLIGHT_OPTION, _option(price_eur=999.0), hotel_option()]
    with respx.mock(assert_all_called=False) as router:
        _mock_site(router)
        router.get(HOTEL_URL).mock(return_value=httpx.Response(200, text=HOTEL_HTML))
        result = CliRunner().invoke(cli, ["read-feed"])

    assert result.exit_code == 0, result.output
    assert "Flights and packages (2 newest posts)" in result.output
    assert "✅ kept: flight: MXP ⇄ MAD, €30, 2026-10-31 → 2026-11-03" in result.output
    assert "– rejected: price €999 is not written in the post" in result.output
    assert "Hotels (1 newest post)" in result.output
    assert "✅ kept: hotel: Arinna Cappadocia, Göreme: €48.5/night per person, October to December" in result.output
    asyncio.run(init_db())
    assert asyncio.run(_cached_rows()) == 0


def test_read_feed_command_explains_why_the_reader_is_off(engine):
    result = CliRunner().invoke(cli, ["read-feed"])

    assert result.exit_code == 1
    assert "The PiratinViaggio reader is off: needs ANTHROPIC_API_KEY" in result.output
