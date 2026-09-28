"""Shared pytest fixtures."""
from __future__ import annotations

import os

import pytest
import respx
import yaml

from tests.harness import (
    DEFAULT_PREFS,
    SEND_MESSAGE_URL,
    TEST_BOT_TOKEN,
    TEST_CHAT_ID,
    Engine,
    TelegramCapture,
    reset_caches,
)


# Override DB path to use a temp file during tests
@pytest.fixture(autouse=True, scope="session")
def test_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("data")
    db_path = str(tmp / "test_travel_deals.db")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    return db_path


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Isolated engine: temp DB + temp preferences + mocked Telegram API."""
    prefs_path = tmp_path / "user_preferences.yaml"
    prefs_path.write_text(yaml.safe_dump(DEFAULT_PREFS))

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'engine.db'}")
    monkeypatch.setenv("CHEAPTRIP_PREFS_PATH", str(prefs_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TEST_BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", TEST_CHAT_ID)
    # Never reach real external services from tests, even if a local .env sets keys.
    for key in ("ANTHROPIC_API_KEY", "RAPIDAPI_KEY", "EXCHANGE_RATE_API_KEY"):
        monkeypatch.setenv(key, "")
    reset_caches()

    capture = TelegramCapture()
    with respx.mock(assert_all_called=False) as router:
        router.post(SEND_MESSAGE_URL).mock(side_effect=capture.record)
        yield Engine(tmp_path=tmp_path, prefs_path=prefs_path, telegram=capture, router=router)

    monkeypatch.undo()
    reset_caches()
