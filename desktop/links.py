"""
cheaptrip:// links and the app's screen routes (B40).

A route is what follows "#/" in the app's address: "deals", "deals/<id>",
"destinations", "activity", "settings" or "setup" ("" is the start screen).
Windows runs `CheapTrip.exe cheaptrip://deal/<id>` for a link; the running app
then shows that screen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence
from urllib.parse import unquote, urlsplit

SCHEME = "cheaptrip"
SCREENS = ("deals", "destinations", "activity", "settings", "setup")
_DEAL_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def clean_route(route: str) -> Optional[str]:
    """The route when the app has that screen ("" for the start screen), else None."""
    parts = [p for p in route.strip().lstrip("#").strip("/").split("/") if p]
    if not parts:
        return ""
    if parts[0] in SCREENS and len(parts) == 1:
        return parts[0]
    if parts[0] in ("deal", "deals") and len(parts) == 2 and _DEAL_ID.match(parts[1]):
        return f"deals/{parts[1]}"
    return None


def route_for_link(link: str) -> Optional[str]:
    """'cheaptrip://deal/abc' → 'deals/abc', 'cheaptrip://activity' → 'activity', 'cheaptrip://' → ''."""
    parts = urlsplit(link.strip())
    if parts.scheme.lower() != SCHEME:
        return None
    path = unquote(f"{parts.netloc}/{parts.path}")
    return "" if path.strip("/") in ("", "open") else clean_route(path)


@dataclass
class LaunchArgs:
    minimized: bool = False
    route: Optional[str] = None  # None: whatever the app shows at start


def parse_args(argv: Sequence[str]) -> LaunchArgs:
    """CheapTrip.exe [--minimized] [cheaptrip://...]; anything else is ignored."""
    args = LaunchArgs()
    for arg in argv:
        if arg == "--minimized":
            args.minimized = True
        elif arg.lower().startswith(f"{SCHEME}:"):
            args.route = route_for_link(arg)
    return args
