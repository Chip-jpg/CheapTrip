"""Every icon the app names is in its icon font (Material Symbols Outlined), so none shows as its name in text."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Set

UI = Path(__file__).resolve().parent.parent / "ui"
ICON_NAMES = UI / "scripts" / "icon-names.txt"  # every icon in the font (ui/scripts/icon_names.py)

# <Icon name="flight" />, <Icon name={on ? "check_box" : "check_box_outline_blank"} />, { icon: "map" }, icon="tune"
NAME_ATTRIBUTE = re.compile(r"""<Icon\b[^>]*?\bname=(?:"([a-z0-9_]+)"|\{([^}]*)\})""", re.S)
ICON_FIELD = re.compile(r"""\bicon(?::\s*|=)"([a-z0-9_]+)\"""")
BRANCH = re.compile(r"""(?:^|[?:])\s*"([a-z0-9_]+)\"""")  # the names a condition picks from, not what it compares


def icons_named_in_ui() -> Set[str]:
    names: Set[str] = set()
    for path in (UI / "src").rglob("*.tsx"):
        if path.name.endswith(".test.tsx"):
            continue
        source = path.read_text(encoding="utf-8")
        for literal, expression in NAME_ATTRIBUTE.findall(source):
            names.update([literal] if literal else BRANCH.findall(expression.strip()))
        names.update(ICON_FIELD.findall(source))
    return names


def test_every_icon_the_app_names_is_in_the_font():
    in_font = set(ICON_NAMES.read_text(encoding="utf-8").split())
    missing = sorted(icons_named_in_ui() - in_font)
    assert not missing, f"not icons in Material Symbols (a typo?): {missing}"


def test_the_scan_finds_the_app_icons():
    named = icons_named_in_ui()
    assert {"radar", "flight", "local_offer", "check_box_outline_blank", "error", "refresh"} <= named
    assert len(named) > 80
