"""The trimmed icon font (B46): every icon the app names is in it, drawn outlined and filled."""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools.ttLib import TTFont

UI = Path(__file__).resolve().parent.parent / "ui"
sys.path.insert(0, str(UI / "scripts"))

from trim_icons import ALL_NAMES, TRIMMED_FONT, alternates, ligatures, strings_in_ui  # noqa: E402


def test_every_icon_the_app_names_is_in_the_font():
    icon_names = set(ALL_NAMES.read_text(encoding="utf-8").split())
    used = strings_in_ui() & icon_names
    in_font = set(ligatures(TTFont(TRIMMED_FONT)))

    missing = sorted(used - in_font)
    assert not missing, f"run `python ui/scripts/trim_icons.py` (after `npm ci` in ui/): {missing} are missing"
    assert {"radar", "flight", "notifications_off", "check_box_outline_blank"} <= used  # the scan finds icons


def test_the_font_keeps_its_filled_icons_and_axes():
    font = TTFont(TRIMMED_FONT)
    icons = set(ligatures(font).values())
    assert [axis.axisTag for axis in font["fvar"].axes] == ["FILL", "GRAD", "opsz", "wght"]
    assert font["GSUB"].table.FeatureVariations is not None  # FILL 1 swaps in the filled versions
    assert len(alternates(font, icons)) > 50
    assert TRIMMED_FONT.stat().st_size < 200_000
