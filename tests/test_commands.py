"""Telegram commands (B20): polling, overrides, pause."""
from __future__ import annotations

from typing import List

import httpx
import pytest

from notifier.commands import CommandBot
from notifier.telegram import TelegramNotifier
from preferences import get_preferences
from storage.database import get_state, init_db
from tests.harness import TEST_BOT_TOKEN, TEST_CHAT_ID, flight

GET_UPDATES = f"https://api.telegram.org/bot{TEST_BOT_TOKEN}/getUpdates"


class Chat:
    """Queues chat messages for getUpdates and sends them through the bot."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.pending: List[dict] = []
        self.next_id = 100
        self.requests: List[httpx.Request] = []
        engine.router.get(url__startswith=GET_UPDATES).mock(side_effect=self._updates)

    def _updates(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result, self.pending = self.pending, []
        return httpx.Response(200, json={"ok": True, "result": result})

    async def say(self, *texts: str, chat_id: str = TEST_CHAT_ID) -> List[str]:
        """Send commands; returns the bot's replies."""
        await init_db()
        for text in texts:
            self.pending.append({"update_id": self.next_id, "message": {"chat": {"id": int(chat_id)}, "text": text}})
            self.next_id += 1
        before = len(self.engine.telegram.texts)
        async with httpx.AsyncClient() as client:
            await CommandBot(await TelegramNotifier.create()).poll_once(client, timeout=0)
        return self.engine.telegram.texts[before:]


@pytest.fixture
def chat(engine):
    return Chat(engine)


def _alerts(engine) -> List[str]:
    return [t for t in engine.telegram.texts if "FLIGHT DEAL" in t]


async def test_help_lists_the_commands(chat):
    [reply] = await chat.say("/help")

    for command in ("/deals", "/status", "/mute", "/priority", "/budget", "/pause", "/resume"):
        assert command in reply


async def test_unknown_command_and_plain_text(chat):
    replies = await chat.say("/fly", "hello")

    assert replies == ["ℹ️ Unknown command /fly. Try /help."]


async def test_messages_from_other_chats_are_ignored(chat):
    assert await chat.say("/help", chat_id="999") == []


async def test_offset_is_stored_so_commands_are_not_replayed(chat):
    await chat.say("/help", "/status")

    assert await get_state("telegram_update_offset") == "102"
    await chat.say()
    assert chat.requests[-1].url.params["offset"] == "102"


async def test_mute_stops_alerts_until_unmute(engine, chat):
    [reply] = await chat.say("/mute krk")
    assert "Muted Krakow (KRK)" in reply

    await engine.run_cycle(flights=[flight(destination="KRK", price=20.0)])
    assert _alerts(engine) == []

    await chat.say("/unmute KRK")
    await engine.run_cycle(flights=[flight(destination="KRK", price=20.0)])
    assert len(_alerts(engine)) == 1


async def test_priority_makes_any_price_instant(engine, chat):
    await chat.say("/priority JFK")

    await engine.run_cycle(flights=[flight(destination="JFK", price=900.0, nights=10)])

    assert len(_alerts(engine)) == 1


async def test_unpriority_removes_a_yaml_priority(engine, chat):
    engine.set_prefs(priority_destinations=["KRK"])

    await chat.say("/unpriority KRK")

    assert get_preferences().priority_destinations == []


async def test_priority_unmutes_and_mute_ends_priority(engine, chat):
    engine.set_prefs(excluded_destinations=["KRK"], priority_destinations=["BCN"])  # both from the YAML file

    await chat.say("/priority KRK", "/mute BCN")

    prefs = get_preferences()
    assert prefs.priority_destinations == ["KRK"] and prefs.excluded_destinations == ["BCN"]
    await engine.run_cycle(flights=[flight(destination="KRK", price=900.0)])
    assert len(_alerts(engine)) == 1  # a priority now, not muted


async def test_priority_and_mute_stay_exclusive_through_overrides(engine, chat):
    await chat.say("/mute PRG", "/priority PRG")  # neither in the YAML file: overrides only
    prefs = get_preferences()
    assert "PRG" in prefs.priority_destinations and "PRG" not in prefs.excluded_destinations

    await chat.say("/mute PRG")
    prefs = get_preferences()
    assert "PRG" in prefs.excluded_destinations and "PRG" not in prefs.priority_destinations


async def test_invalid_airport_code(chat):
    [reply] = await chat.say("/mute XQZ")

    assert "Usage: /mute KRK" in reply


async def test_budget_on_and_off(chat):
    await chat.say("/budget 300")
    assert get_preferences().max_trip_budget == 300.0

    await chat.say("/budget off")
    assert get_preferences().max_trip_budget is None


async def test_pause_holds_alerts_and_resume_sends_them(engine, chat):
    [reply] = await chat.say("/pause 2")
    assert "Alerts paused until" in reply

    await engine.run_cycle(flights=[flight(destination="KRK", price=20.0)])
    assert _alerts(engine) == []

    await chat.say("/resume")
    await engine.run_cycle(flights=[])  # the pending instant alert goes out
    assert len(_alerts(engine)) == 1


async def test_digest_is_held_while_paused(engine, chat):
    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)])
    await chat.say("/pause")

    await engine.run_digest()

    assert not any("DAILY TRAVEL DEALS DIGEST" in t for t in engine.telegram.texts)


async def test_deals_status_and_health(engine, chat):
    [empty] = await chat.say("/deals")
    assert "No deals in the last 24 hours" in empty

    await engine.run_cycle(flights=[flight(destination="KRK", price=300.0)])
    deals, status, health = await chat.say("/deals", "/status", "/health")

    assert "Top deals, last 24h" in deals and "Milan → Krakow" in deals
    assert "Last cycle:" in status and "not yet" not in status
    assert "Deals saved today: 1" in status
    assert health.startswith("ℹ️ <pre>")
