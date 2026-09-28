"""
Alert message formatting.

All messages are Telegram HTML (parse_mode=HTML). Every dynamic value goes
through _e() / _url() so hotel names, airlines or URLs containing <, > or &
can't break the markup. Prices, discounts and verdicts are computed upstream;
the optional AI polish (enhance_with_ai) may only reword, and is rejected if
any number, airport code or link changes.
"""
from __future__ import annotations

import html
import re
from collections import Counter
from typing import Any, List, Optional

from storage.models import BookingConfidence, DealType, Trip
from utils.logging_config import get_logger

log = get_logger(__name__)

_BC_EMOJI = {
    BookingConfidence.HIGH: "🟢",
    BookingConfidence.MEDIUM: "🟡",
    BookingConfidence.LOW: "🔴",
}

_VERDICT_EMOJI = {"BOOK NOW": "🟢", "VERIFY & BOOK": "🟡", "GOOD DEAL": "🔵", "STRONG DEAL": "🔵"}

# Telegram's HTML parse mode supports only these tags.
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre", "blockquote"}


def _e(value: Any) -> str:
    """Escape text for Telegram HTML."""
    return html.escape(str(value), quote=False)


def _url(value: str) -> str:
    return html.escape(value, quote=True)


def _link(url: str, label: str = "🔗 Book Now") -> str:
    return f'<a href="{_url(url)}">{_e(label)}</a>'


def _verdict_line(trip: Trip) -> str:
    base = trip.verdict.split(" (")[0]
    return f"<b>Verdict:</b> {_VERDICT_EMOJI.get(base, '⚪')} {_e(trip.verdict)}"


def _confidence_line(trip: Trip) -> str:
    confidence = BookingConfidence(trip.booking_confidence)
    emoji = _BC_EMOJI.get(confidence, "⚪")
    return (
        f"<b>Booking confidence:</b> {emoji} {confidence.value}  "
        f"<i>(data: {trip.data_confidence_score:.2f})</i>"
    )


def _category_line(trip: Trip) -> str:
    return f"<b>Category:</b> {_e(trip.category.value)}" if trip.category else ""


def _airports_line(trip: Trip) -> str:
    leg = trip.outbound_flight
    return f"<code>{_e(leg.origin)} → {_e(leg.destination)}</code>" if leg else ""


def _reasons_line(trip: Trip) -> str:
    reasons = [r for r in trip.alert_reasons if not r.startswith("held back")]
    return f"<i>Why: {_e('; '.join(reasons))}</i>" if reasons else ""


def _cached_price_line(trip: Trip) -> str:
    if trip.outbound_flight and trip.outbound_flight.price_is_cached:
        return "ℹ️ <i>Cached price from recent searches — verify before booking</i>"
    return ""


def _dates_line(trip: Trip) -> str:
    if not trip.departure_date:
        window = trip.outbound_flight.travel_window if trip.outbound_flight else None
        return f"<b>Dates:</b> {_e(window) if window else 'various'} — see the deal post"
    dep = trip.departure_date.strftime("%a %d %b")
    if trip.return_date:
        nights = f" ({trip.nights} night{'s' if trip.nights != 1 else ''})" if trip.nights else ""
        return f"<b>Dates:</b> {dep} – {trip.return_date.strftime('%a %d %b')}{nights}"
    return f"<b>Departure:</b> {dep}"


def _join(lines: List[str]) -> str:
    """Join lines, collapsing runs of blank lines left by empty optional sections."""
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── Deterministic formatters ─────────────────────────────────────────────────

def format_complete_trip(trip: Trip) -> str:
    """Format a complete trip (flight + hotel) alert message."""
    lines = ["🔥 <b>TOP COMPLETE TRIP</b>", ""]
    lines.append(f"<b>Route:</b> {_e(trip.route)}")
    lines.append(_airports_line(trip))
    lines.append("")

    if trip.outbound_flight:
        flight = f"<b>Flights:</b> €{trip.flight_cost_eur:.0f}"
        if trip.outbound_flight.airline:
            flight += f" <i>({_e(trip.outbound_flight.airline)})</i>"
        lines.append(flight)

    if trip.hotel:
        nights = trip.nights or trip.hotel.nights
        lines.append(
            f"<b>Hotel ({nights} nights):</b> €{trip.hotel_cost_eur:.0f} "
            f"(€{trip.hotel.price_per_night_eur:.0f}/night)"
        )
        details = []
        if trip.hotel.name and trip.hotel.name != "Unknown":
            details.append(f"<i>{_e(trip.hotel.name)}</i>")
        if trip.hotel.rating:
            details.append(f"⭐ {trip.hotel.rating:.1f}")
        if details:
            lines.append(" · ".join(details))
    lines.append("")

    lines.append(f"<b>TOTAL:</b> 💰 <b>€{trip.total_cost_eur:.0f}</b>")
    if trip.normal_price_eur:
        normal = f"<b>Normal price:</b> ~€{trip.normal_price_eur:.0f}+"
        if trip.discount_pct:
            normal += f" · <b>You save:</b> {trip.discount_pct:.0f}%"
        lines.append(normal)
    lines.append(_dates_line(trip))
    lines.append("")

    lines.append(_verdict_line(trip))
    lines.append(_category_line(trip))
    lines.append(_confidence_line(trip))
    lines.append(_reasons_line(trip))

    if trip.is_error_fare:
        lines.append("⚠️ <i>Possible error fare — book fast, verify after</i>")
    if not trip.is_feasible and trip.feasibility_notes:
        lines.append(f"⚠️ <i>Feasibility: {_e('; '.join(trip.feasibility_notes))}</i>")
    if trip.is_historical_low:
        lines.append("🏆 <i>Historical price low!</i>")
    elif trip.historical_deviation_pct and trip.historical_deviation_pct < -15:
        lines.append(f"📉 <i>{abs(trip.historical_deviation_pct):.0f}% below 30-day average</i>")
    lines.append(_cached_price_line(trip))

    links = []
    if trip.outbound_flight and trip.outbound_flight.booking_url:
        links.append(_link(trip.outbound_flight.booking_url, "🔗 Book flight"))
    if trip.hotel and trip.hotel.booking_url:
        links.append(_link(trip.hotel.booking_url, "🏨 Book hotel"))
    if links:
        lines.append("")
        lines.append("  ".join(links))

    return _join(lines)


def format_flight_only(trip: Trip) -> str:
    """Format a flight-only deal alert."""
    lines = ["✈️ <b>FLIGHT DEAL</b>", ""]
    lines.append(f"<b>Route:</b> {_e(trip.route)}")
    lines.append(_airports_line(trip))
    lines.append("")

    lines.append(f"<b>Price:</b> 💰 <b>€{trip.total_cost_eur:.0f}</b>")
    if trip.normal_price_eur:
        normal = f"<b>Normal:</b> ~€{trip.normal_price_eur:.0f}+"
        if trip.discount_pct:
            normal += f" · <b>Saving:</b> {trip.discount_pct:.0f}%"
        lines.append(normal)

    if trip.outbound_flight and trip.outbound_flight.airline:
        lines.append(f"<b>Airline:</b> {_e(trip.outbound_flight.airline)}")
    lines.append(_dates_line(trip))
    lines.append("")

    lines.append(_verdict_line(trip))
    lines.append(_category_line(trip))
    lines.append(_confidence_line(trip))
    lines.append(_reasons_line(trip))

    if trip.is_error_fare:
        lines.append("⚠️ <i>Possible error fare</i>")
    if trip.is_historical_low:
        lines.append("🏆 <i>Historical price low!</i>")
    lines.append(_cached_price_line(trip))

    if trip.outbound_flight and trip.outbound_flight.booking_url:
        lines.append("")
        lines.append(_link(trip.outbound_flight.booking_url))

    return _join(lines)


def format_hotel_only(trip: Trip) -> str:
    """Format a hotel-only deal alert."""
    lines = ["🏨 <b>HOTEL DEAL</b>", ""]
    if trip.hotel:
        lines.append(f"<b>{_e(trip.hotel.name)}</b>")
        lines.append(f"📍 {_e(trip.hotel.location)}")
        lines.append("")
        lines.append(f"<b>Price:</b> 💰 <b>€{trip.hotel.price_per_night_eur:.0f}/night</b>")
        if trip.discount_pct:
            lines.append(f"<b>Discount:</b> -{trip.discount_pct:.0f}%")
        if trip.hotel.rating:
            lines.append(f"⭐ {trip.hotel.rating:.1f}")
    lines.append("")
    lines.append(_category_line(trip))
    lines.append(_confidence_line(trip))
    lines.append(_reasons_line(trip))
    if trip.hotel and trip.hotel.booking_url:
        lines.append("")
        lines.append(_link(trip.hotel.booking_url))
    return _join(lines)


def format_repositioned(trip: Trip) -> str:
    """Format a repositioned multi-leg trip alert."""
    lines = ["🔀 <b>REPOSITIONED DEAL</b>", ""]
    lines.append(f"<b>Route:</b> {_e(trip.route)}")
    lines.append("")

    hub = trip.repositioning_legs[0].hub if trip.repositioning_legs else "?"
    if trip.repositioning_legs:
        repo = trip.repositioning_legs[0]
        lines.append(f"<b>Leg 1 (repositioning):</b> {_e(repo.origin)} → {_e(repo.hub)}: €{repo.price_eur:.0f}")
    if trip.outbound_flight:
        lines.append(
            f"<b>Leg 2 (main flight):</b> {_e(hub)} → {_e(trip.outbound_flight.destination)}: "
            f"€{trip.outbound_flight.price_eur:.0f}"
        )
    lines.append("")
    lines.append(f"<b>TOTAL:</b> 💰 <b>€{trip.total_cost_eur:.0f}</b>")
    lines.append(_dates_line(trip))
    lines.append("")
    lines.append(_verdict_line(trip))
    lines.append(_category_line(trip))
    lines.append(_confidence_line(trip))
    lines.append(_reasons_line(trip))
    lines.append("<i>Book each leg separately as independent tickets</i>")
    if trip.outbound_flight and trip.outbound_flight.booking_url:
        lines.append("")
        lines.append(_link(trip.outbound_flight.booking_url, "🔗 Book main flight"))
    return _join(lines)


def format_digest(trips: List[Trip]) -> str:
    """Format the daily digest message."""
    lines = ["📋 <b>DAILY TRAVEL DEALS DIGEST</b>", ""]
    lines.append(f"<i>{len(trips)} deals found today</i>")
    lines.append("─" * 20)

    for i, trip in enumerate(trips[:15], 1):
        discount_str = f" (-{trip.discount_pct:.0f}%)" if trip.discount_pct else ""
        deal_icon = "🏨+✈️" if trip.hotel and trip.outbound_flight else ("🏨" if trip.hotel else "✈️")
        confidence = BookingConfidence(trip.booking_confidence)
        bc_emoji = _BC_EMOJI.get(confidence, "⚪")
        cat_str = f" | {_e(trip.category.value)}" if trip.category else ""
        link = trip.outbound_flight.booking_url if trip.outbound_flight else None
        route = _e(trip.route)
        if link:
            route = f'<a href="{_url(link)}">{route}</a>'
        lines.append(
            f"{i}. {route}\n"
            f"   💰 €{trip.total_cost_eur:.0f}{discount_str} | {deal_icon}{cat_str} | "
            f"{bc_emoji} {confidence.value}"
        )

    return "\n".join(lines)


def format_best_finds_summary(trips: List[Trip], total_found: int) -> str:
    """Cycle summary sent when nothing qualified for an instant alert."""
    lines = ["🔍 <b>Cycle Summary — Cheapest Finds</b>", ""]
    for t in sorted(trips, key=lambda t: t.total_cost_eur)[:10]:
        emoji = "🏨+✈️" if t.hotel and t.outbound_flight else ("🏨" if t.hotel else "✈️")
        line = f"{emoji} {_e(t.route)} — <b>€{t.total_cost_eur:.0f}</b>"
        airline = t.outbound_flight.airline if t.outbound_flight else None
        if airline:
            line += f" ({_e(airline)})"
        lines.append(line)
    lines.append("")
    lines.append(f"<i>{total_found} total deals found this cycle</i>")
    return "\n".join(lines)


def select_formatter(trip: Trip):
    """Return the correct formatter function for a trip."""
    if trip.deal_type == DealType.COMPLETE_TRIP:
        return format_complete_trip
    if trip.deal_type in (DealType.FLIGHT_ONLY, DealType.ERROR_FARE):
        return format_flight_only
    if trip.deal_type == DealType.HOTEL_ONLY:
        return format_hotel_only
    if trip.deal_type == DealType.REPOSITIONED:
        return format_repositioned
    return format_flight_only


def html_to_plain(text: str) -> str:
    """Strip tags and unescape entities — fallback when Telegram rejects the HTML."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


# ── Optional AI enhancement (formatting/summarization only) ──────────────────

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_IATA_RE = re.compile(r"\b[A-Z]{3}\b")
_HREF_RE = re.compile(r'href="([^"]*)"')
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)[^>]*?(/?)>")

_AI_SYSTEM_PROMPT = (
    "You polish travel-deal alerts for a Telegram chat. The input is a message in Telegram HTML "
    "(tags: <b>, <i>, <code>, <a href>). Improve readability and emoji use only. "
    "Keep every price, percentage, date, number, airport code and link exactly as written, "
    "keep all links as <a href> tags with the same URL, use only the tags listed above, "
    "and do not add any information. Reply with the message text only."
)


def _tags_are_valid(text: str) -> bool:
    """Only Telegram-supported tags, properly nested."""
    stack: List[str] = []
    for closing, name, self_closing in _TAG_RE.findall(text):
        name = name.lower()
        if name not in ALLOWED_TAGS or self_closing:
            return False
        if closing:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


def ai_output_is_safe(base_message: str, enhanced: str) -> bool:
    """The polished text must keep every number, airport code and link, and valid HTML."""
    if not enhanced.strip():
        return False
    visible_base, visible_new = html_to_plain(base_message), html_to_plain(enhanced)
    if Counter(_NUMBER_RE.findall(visible_base)) != Counter(_NUMBER_RE.findall(visible_new)):
        return False
    if set(_IATA_RE.findall(visible_base)) != set(_IATA_RE.findall(visible_new)):
        return False
    if sorted(_HREF_RE.findall(base_message)) != sorted(_HREF_RE.findall(enhanced)):
        return False
    return _tags_are_valid(enhanced)


def _make_client(api_key: str, timeout: float) -> Any:
    import anthropic

    return anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)


async def enhance_with_ai(trip: Trip, base_message: str) -> str:
    """
    Optionally pass the base message through Claude for polish.
    Falls back to base_message if AI is unavailable, errors, refuses, or
    changes anything it must not change.
    """
    from config import get_settings

    settings = get_settings()
    if not settings.anthropic_api_key:
        return base_message

    enhanced: Optional[str] = None
    try:
        client = _make_client(settings.anthropic_api_key, settings.ai_timeout_seconds)
        response = await client.messages.create(
            model=settings.ai_model,
            max_tokens=1024,
            system=_AI_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": base_message}],
        )
        if response.stop_reason == "refusal":
            log.warning("ai_enhance_refused", route=trip.route)
            return base_message
        enhanced = next((b.text for b in response.content if b.type == "text"), "").strip()
    except Exception as exc:
        log.warning("ai_enhance_failed", error=str(exc), route=trip.route)
        return base_message

    if not ai_output_is_safe(base_message, enhanced):
        log.warning("ai_output_rejected_fallback", route=trip.route)
        return base_message
    return enhanced
