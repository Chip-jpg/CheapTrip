"""Running as the installed Windows app (B35): version, app home, encodings."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

from click.testing import CliRunner

from main import cli
from utils import paths
from utils.version import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    with open(ROOT / "pyproject.toml", "rb") as f:
        assert tomllib.load(f)["project"]["version"] == __version__ == "0.8.2"


def test_version_option():
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0 and result.output.strip() == "CheapTrip, version 0.8.2"


# ── App home ──────────────────────────────────────────────────────────────────

def test_source_checkout_and_docker_keep_working_from_the_current_folder(monkeypatch):
    monkeypatch.delenv("CHEAPTRIP_HOME", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert paths.app_home() is None
    assert paths.enter_app_home() is None


def test_installed_app_lives_in_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("CHEAPTRIP_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert paths.app_home() == tmp_path / "CheapTrip"


def test_cheaptrip_home_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("CHEAPTRIP_HOME", str(tmp_path / "elsewhere"))

    assert paths.app_home() == tmp_path / "elsewhere"


def test_entering_the_home_seeds_settings_and_points_preferences_there(monkeypatch, tmp_path):
    home = tmp_path / "CheapTrip"
    monkeypatch.chdir(tmp_path)  # restored after the test
    monkeypatch.setenv("CHEAPTRIP_HOME", str(home))
    monkeypatch.setenv("CHEAPTRIP_PREFS_PATH", "unset")
    monkeypatch.delenv("CHEAPTRIP_PREFS_PATH")

    assert paths.enter_app_home() == home

    assert Path.cwd() == home
    prefs = home / "config" / "user_preferences.yaml"
    assert os.environ["CHEAPTRIP_PREFS_PATH"] == str(prefs)
    assert prefs.read_text(encoding="utf-8").startswith(
        (ROOT / "config" / "user_preferences.yaml.example").read_text(encoding="utf-8")[:40])
    assert "TELEGRAM_BOT_TOKEN=" in (home / ".env").read_text(encoding="utf-8")


def test_existing_settings_are_never_overwritten(monkeypatch, tmp_path):
    home = tmp_path / "CheapTrip"
    (home / "config").mkdir(parents=True)
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=mine\n", encoding="utf-8")
    (home / "config" / "user_preferences.yaml").write_text("home_airports: [LGW]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHEAPTRIP_HOME", str(home))

    paths.enter_app_home()

    assert (home / ".env").read_text(encoding="utf-8") == "TELEGRAM_BOT_TOKEN=mine\n"
    assert (home / "config" / "user_preferences.yaml").read_text(encoding="utf-8") == "home_airports: [LGW]\n"


# ── Encodings (Windows defaults to its code page, not UTF-8) ──────────────────

def test_settings_saved_with_a_bom_still_load(monkeypatch, tmp_path):
    from config import Settings

    (tmp_path / ".env").write_bytes("TELEGRAM_CHAT_ID=4242\n".encode("utf-8-sig"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert Settings().telegram_chat_id == "4242"


def test_preferences_saved_with_a_bom_and_accents_still_load(tmp_path):
    from preferences import load_preferences

    path = tmp_path / "prefs.yaml"
    path.write_bytes("home_airports: [KRK]  # Kraków\n".encode("utf-8-sig"))

    assert load_preferences(path).home_airports == ["KRK"]
