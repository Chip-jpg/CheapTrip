"""
The picture at the top of a deal's Windows notification (Round 8, as in the
design's toast gallery): the airports, the price in large type, how far below
the usual price it is, and a sparkline of similar fares around its date.

Drawn with Pillow at twice Windows' 364×180 so it stays sharp; files go to
data/toasts/ (the newest few are kept). Anything that fails just leaves the
notification without a picture.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from storage.models import Trip
from utils.logging_config import get_logger

log = get_logger(__name__)

SIZE = (728, 360)
KEEP = 20
COLORS = {
    "background": (32, 32, 32), "panel": (42, 42, 42), "text": (229, 226, 225), "muted": (136, 146, 154),
    "deal": (74, 223, 161), "caution": (255, 180, 171), "usual": (255, 201, 136), "accent": (161, 218, 255),
}
# Windows' own fonts first (the app's platform), then common ones elsewhere; all have € and →
FONTS = {
    "regular": ["segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"],
    "bold": ["segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
    "mono": ["consola.ttf", "DejaVuSansMono.ttf", "Menlo.ttc"],
}
_PLAIN = {"€": "EUR ", "→": "-", "±": "+/-"}  # for Pillow's own font, which has neither € nor →


class _Writer:
    """Text in the best font found; without one, Pillow's own font and plain symbols."""

    def __init__(self, draw: Any) -> None:
        self.draw = draw
        self.fallback = False

    def font(self, kind: str, size: int) -> Any:
        from PIL import ImageFont

        for name in FONTS[kind]:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        self.fallback = True
        return ImageFont.load_default(size=size)

    def text(self, xy: tuple, text: str, kind: str, size: int, fill: tuple) -> None:
        font = self.font(kind, size)
        if self.fallback:
            for symbol, plain in _PLAIN.items():
                text = text.replace(symbol, plain)
        self.draw.text(xy, text, font=font, fill=fill)


def _sparkline(draw: Any, box: Sequence[int], prices: List[float], current: int, color: tuple,
               median: Optional[float]) -> None:
    left, top, right, bottom = box
    values = prices + ([median] if median is not None else [])
    hi, lo = max(values) * 1.05, min(values) * 0.6
    def x(i: int) -> float:
        return left + (right - left) * (i / (len(prices) - 1) if len(prices) > 1 else 0.5)

    def y(price: float) -> float:
        return bottom - (price - lo) / ((hi - lo) or 1) * (bottom - top)

    if median is not None:
        for start in range(left, right, 14):  # dashed: the usual price
            draw.line([(start, y(median)), (min(start + 7, right), y(median))], fill=COLORS["usual"], width=2)
    draw.line([(x(i), y(p)) for i, p in enumerate(prices)], fill=color, width=4, joint="curve")
    cx, cy = x(current), y(prices[current])
    draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=color)


def render(trip: Trip, comparison: Optional[Dict[str, Any]], folder: Path) -> Optional[Path]:
    """The picture for this deal, or None (no dated flight, or it couldn't be drawn)."""
    leg = trip.outbound_flight
    if leg is None:
        return None
    try:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", SIZE, COLORS["background"])
        draw = ImageDraw.Draw(image)
        write = _Writer(draw)
        tone = COLORS["caution"] if trip.is_error_fare else COLORS["deal"] if trip.discount_pct else COLORS["accent"]

        write.text((40, 34), f"{leg.origin} → {leg.destination}", "mono", 34, COLORS["muted"])
        write.text((40, 80), f"€{trip.total_cost_eur:,.0f}", "bold", 110, tone)
        if trip.discount_pct and trip.normal_price_eur:
            line = f"{trip.discount_pct:.0f}% below the usual €{trip.normal_price_eur:,.0f}"
        elif trip.previous_price_eur and trip.previous_price_eur > trip.total_cost_eur:
            line = f"Dropped from €{trip.previous_price_eur:,.0f}"
        else:
            line = "Per person, return" if trip.return_date else "Per person, one way"
        write.text((40, 222), line, "regular", 34, COLORS["text"])
        if trip.is_error_fare:
            write.text((40, 272), "Possible error fare: book fast", "bold", 30, COLORS["caution"])

        points = (comparison or {}).get("points") or []
        if len(points) >= 3:
            prices = [p["price"] for p in points]
            current = next((i for i, p in enumerate(points) if p.get("is_this")), len(points) - 1)
            draw.rounded_rectangle([420, 60, 690, 200], radius=16, fill=COLORS["panel"])
            _sparkline(draw, (440, 84, 670, 176), prices, current, tone, comparison.get("median"))
            write.text((440, 208), "Similar fares ±30 days", "regular", 21, COLORS["muted"])

        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{trip.hash or 'deal'}.png"
        image.save(path, "PNG")
        for old in sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime)[:-KEEP]:
            old.unlink(missing_ok=True)
        return path
    except Exception as exc:
        log.warning("toast_image_failed", error=str(exc), route=trip.route)
        return None
