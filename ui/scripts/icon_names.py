"""
List every icon in the Material Symbols font the app uses (ui/scripts/icon-names.txt).

    pip install fonttools brotli
    python ui/scripts/icon_names.py        # after `npm ci` in ui/, when material-symbols is upgraded

Icons are written by name (<Icon name="flight" />) and the font turns each name
into its icon (a ligature), so a mistyped name shows as text. The list lets a
test (tests/test_icons.py) check every name the app uses without node_modules.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Set

from fontTools.ttLib import TTFont

UI = Path(__file__).resolve().parent.parent
FONT = UI / "node_modules" / "material-symbols" / "material-symbols-outlined.woff2"
ALL_NAMES = UI / "scripts" / "icon-names.txt"


def ligature_names(font: TTFont) -> Set[str]:
    """Every icon name, from the font's ligature table."""
    char_of = {glyph: chr(code) for code, glyph in font.getBestCmap().items()}
    names: Set[str] = set()
    for lookup in font["GSUB"].table.LookupList.Lookup:
        for table in lookup.SubTable:
            if table.LookupType == 7:  # an extension: the real table inside
                table = table.ExtSubTable
            if table.LookupType != 4:
                continue
            for first, rules in table.ligatures.items():
                for rule in rules:
                    names.add(char_of.get(first, "") + "".join(char_of.get(g, "") for g in rule.Component))
    return names


def main() -> None:
    if not FONT.exists():
        sys.exit(f"{FONT} is missing: run `npm ci` in ui/ first")
    names = ligature_names(TTFont(FONT))
    ALL_NAMES.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    print(f"{len(names)} icons listed in {ALL_NAMES}")


if __name__ == "__main__":
    main()
