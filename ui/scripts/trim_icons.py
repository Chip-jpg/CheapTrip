"""
Trim the Material Symbols icon font to the icons the app uses (B46): 4 MB → ~100 KB.

    pip install fonttools brotli
    python ui/scripts/trim_icons.py        # after `npm ci` in ui/, and whenever an icon is added

Icons are written by name ("<Icon name="flight" />", icon: "tune", ...), and the
font turns each name into its icon (a ligature). This keeps every icon whose
name is a whole string anywhere in ui/src — a generous superset, so names
passed through props and data are kept too — with all four axes (fill,
weight, grade, optical size). It writes:

- ui/src/assets/icons.woff2: the trimmed font theme.css uses;
- ui/scripts/icon-names.txt: every icon name in the full font, so a test can
  check, without node_modules, that no icon used in ui/src is missing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict

from fontTools import subset
from fontTools.ttLib import TTFont

UI = Path(__file__).resolve().parent.parent
FULL_FONT = UI / "node_modules" / "material-symbols" / "material-symbols-outlined.woff2"
TRIMMED_FONT = UI / "src" / "assets" / "icons.woff2"
ALL_NAMES = UI / "scripts" / "icon-names.txt"
NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_"


def ligatures(font: TTFont) -> Dict[str, str]:
    """Icon name → its glyph, from the font's ligature table."""
    char_of = {glyph: chr(code) for code, glyph in font.getBestCmap().items()}
    names: Dict[str, str] = {}
    for lookup in font["GSUB"].table.LookupList.Lookup:
        for table in lookup.SubTable:
            if table.LookupType == 7:  # an extension: the real table inside
                table = table.ExtSubTable
            if table.LookupType != 4:
                continue
            for first, rules in table.ligatures.items():
                for rule in rules:
                    name = char_of.get(first, "") + "".join(char_of.get(g, "") for g in rule.Component)
                    names[name] = rule.LigGlyph
    return names


def alternates(font: TTFont, glyphs: set) -> set:
    """The filled versions of these icons: the font swaps them in when FILL is 1 (single substitutions)."""
    found = set()
    for lookup in font["GSUB"].table.LookupList.Lookup:
        for table in lookup.SubTable:
            if table.LookupType == 7:
                table = table.ExtSubTable
            if table.LookupType == 1:
                found.update(table.mapping[glyph] for glyph in glyphs if glyph in table.mapping)
    return found


def strings_in_ui(src: Path = UI / "src") -> set:
    """Every whole-word string ("flight", 'tune', `menu`) in the app's code: icon names among them."""
    strings = set()
    for path in src.rglob("*"):
        if path.suffix in (".ts", ".tsx") and not path.name.endswith((".test.ts", ".test.tsx")):
            for quoted in re.findall(r""""([a-z][a-z0-9_]*)"|'([a-z][a-z0-9_]*)'|`([a-z][a-z0-9_]*)`""",
                                     path.read_text(encoding="utf-8")):
                strings.update(word for word in quoted if word)
    return strings


def main() -> None:
    if not FULL_FONT.exists():
        sys.exit(f"{FULL_FONT} is missing: run `npm ci` in ui/ first")
    font = TTFont(FULL_FONT)
    names = ligatures(font)
    used = sorted(strings_in_ui() & set(names))

    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]  # the icons are "rlig" (required ligatures)
    options.layout_closure = False  # only the icons named, not every ligature their letters could make
    subsetter = subset.Subsetter(options)
    icons = {names[name] for name in used}
    subsetter.populate(glyphs=icons | alternates(font, icons), text=NAME_CHARS)
    subsetter.subset(font)
    TRIMMED_FONT.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.save(TRIMMED_FONT)
    ALL_NAMES.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    print(f"{len(used)} icons kept: {TRIMMED_FONT.stat().st_size / 1000:.0f} KB "
          f"(the full font is {FULL_FONT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
