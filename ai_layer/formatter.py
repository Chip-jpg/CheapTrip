from __future__ import annotations

from typing import List

from storage.models import BookingConfidence, DealType, Trip
from utils.logging_config import get_logger

log = get_logger(__name__)

_BC_EMOJI = {
    BookingConfidence.HIGH: "🟢",
    BookingConfidence.MEDIUM: "🟡",
    BookingConfidence.LOW: "🔴",
}


def _confidence_line(trip: Trip) -> str:
    emoji = _BC_EMOJI.get(trip.booking_confidence, "⚪")
    label = f"{emoji} {trip.booking_confidence.value}"
    return f"*Booking confidence:* {label}  _(data: {trip.data_confidence_score:.2f})_"


def _category_line(trip: Trip) -> str:
    if trip.category:
        return f"*Category:* {trip.category.value}"
    return ""

# ── Deterministic formatters (no AI needed for standard templates) ────────────

def format_complete_trip(trip: Trip) -> str:
    """Format a complete trip (flight + hotel) alert message."""
    lines = ["🔥 *TOP COMPLETE TRIP*\n"]
    lines.append(f"*Route:*\n{trip.route}\n")

    if trip.outbound_flight:
        lines.append(f"*Flights:*\n€{trip.flight_cost_eur:.0f}")
        if trip.outbound_flight.airline:
            lines.append(f"_{trip.outbound_flight.airline}_")
    lines.append("")

    if trip.hotel:
        nights = trip.nights or trip.hotel.nights
        lines.append(
            f"*Hotel ({nights} nights):*\n"
            f"€{trip.hotel_cost_eur:.0f} "
            f"(€{trip.hotel.price_per_night_eur:.0f}/night)"
        )
        if trip.hotel.name and trip.hotel.name != "Unknown":
            lines.append(f"_{trip.hotel.name}_")
        if trip.hotel.rating:
            lines.append(f"⭐ {trip.hotel.rating:.1f}")
    lines.append("")

    lines.append(f"*TOTAL:*\n💰 *€{trip.total_cost_eur:.0f}*\n")

    if trip.normal_price_eur:
        lines.append(f"*Normal price:*\n~€{trip.normal_price_eur:.0f}+")
        if trip.discount_pct:
            lines.append(f"*You save:* {trip.discount_pct:.0f}%")
    lines.append("")

    if trip.departure_date:
        dep_str = trip.departure_date.strftime("%a %d %b")
        if trip.return_date:
            ret_str = trip.return_date.strftime("%a %d %b")
            lines.append(f"*Dates:*\n{dep_str} – {ret_str}")
        else:
            lines.append(f"*Departure:*\n{dep_str}")
    lines.append("")

    verdict_emoji = {"BOOK NOW": "🟢", "VERIFY & BOOK": "🟡", "GOOD DEAL": "🔵"}.get(
        trip.verdict, "⚪"
    )
    lines.append(f"*Verdict:*\n{verdict_emoji} {trip.verdict}")
    cat = _category_line(trip)
    if cat:
        lines.append(cat)
    lines.append(_confidence_line(trip))

    if trip.is_error_fare:
        lines.append("\n⚠️ _Possible error fare — book fast, verify after_")

    if not trip.is_feasible and trip.feasibility_notes:
        lines.append(f"\n⚠️ _Feasibility: {'; '.join(trip.feasibility_notes)}_")

    if trip.is_historical_low:
        lines.append("\n🏆 _Historical price low!_")
    elif trip.historical_deviation_pct and trip.historical_deviation_pct < -15:
        lines.append(f"\n📉 _{abs(trip.historical_deviation_pct):.0f}% below 30-day average_")

    if trip.outbound_flight and trip.outbound_flight.booking_url:
        lines.append(f"\n[🔗 Book Now]({trip.outbound_flight.booking_url})")

    return "\n".join(lines)


def format_flight_only(trip: Trip) -> str:
    """Format a flight-only deal alert."""
    lines = ["✈️ *FLIGHT DEAL*\n"]
    lines.append(f"*Route:*\n{trip.route}\n")

    lines.append(f"*Price:*\n💰 *€{trip.total_cost_eur:.0f}*")

    if trip.normal_price_eur:
        lines.append(f"\n*Normal:*\n~€{trip.normal_price_eur:.0f}+")
        if trip.discount_pct:
            lines.append(f"*Saving:* {trip.discount_pct:.0f}%")

    if trip.outbound_flight:
        if trip.outbound_flight.airline:
            lines.append(f"\n*Airline:* {trip.outbound_flight.airline}")
        if trip.departure_date:
            lines.append(f"*Departure:* {trip.departure_date.strftime('%a %d %b')}")

    verdict_emoji = {"BOOK NOW": "🟢", "VERIFY & BOOK": "🟡"}.get(trip.verdict, "⚪")
    lines.append(f"\n*Verdict:* {verdict_emoji} {trip.verdict}")
    cat = _category_line(trip)
    if cat:
        lines.append(cat)
    lines.append(_confidence_line(trip))

    if trip.is_error_fare:
        lines.append("\n⚠️ _Possible error fare_")

    if trip.is_historical_low:
        lines.append("🏆 _Historical price low!_")

    if trip.outbound_flight and trip.outbound_flight.booking_url:
        lines.append(f"\n[🔗 Book Now]({trip.outbound_flight.booking_url})")

    return "\n".join(lines)


def format_hotel_only(trip: Trip) -> str:
    """Format a hotel-only deal alert."""
    lines = ["🏨 *HOTEL DEAL*\n"]
    if trip.hotel:
        lines.append(f"*{trip.hotel.name}*")
        lines.append(f"📍 {trip.hotel.location}\n")
        lines.append(f"*Price:*\n💰 *€{trip.hotel.price_per_night_eur:.0f}/night*")
        if trip.discount_pct:
            lines.append(f"*Discount:* -{trip.discount_pct:.0f}%")
        if trip.hotel.rating:
            lines.append(f"⭐ {trip.hotel.rating:.1f}")
        if trip.hotel.booking_url:
            lines.append(f"\n[🔗 Book Now]({trip.hotel.booking_url})")

    cat = _category_line(trip)
    if cat:
        lines.append(f"\n{cat}")
    lines.append(_confidence_line(trip))
    return "\n".join(lines)


def format_repositioned(trip: Trip) -> str:
    """Format a repositioned multi-leg trip alert."""
    lines = ["🔀 *REPOSITIONED DEAL*\n"]
    lines.append(f"*Route:*\n{trip.route}\n")

    if trip.repositioning_legs:
        repo = trip.repositioning_legs[0]
        lines.append(f"*Leg 1 (repositioning):*\n{repo.origin} → {repo.hub}: €{repo.price_eur:.0f}")

    if trip.outbound_flight:
        hub = trip.repositioning_legs[0].hub if trip.repositioning_legs else "?"
        lines.append(
            f"*Leg 2 (main flight):*\n{hub} → {trip.outbound_flight.destination}: "
            f"€{trip.outbound_flight.price_eur:.0f}"
        )

    lines.append(f"\n*TOTAL:*\n💰 *€{trip.total_cost_eur:.0f}*\n")

    if trip.departure_date:
        lines.append(f"*Departure:* {trip.departure_date.strftime('%a %d %b')}")

    verdict_emoji = {"BOOK NOW": "🟢", "VERIFY & BOOK": "🟡"}.get(trip.verdict, "⚪")
    lines.append(f"\n*Verdict:* {verdict_emoji} {trip.verdict}")
    cat = _category_line(trip)
    if cat:
        lines.append(cat)
    lines.append(_confidence_line(trip))
    lines.append("\n_Book each leg separately as independent tickets_")

    return "\n".join(lines)


def format_digest(trips: List[Trip]) -> str:
    """Format the daily digest message."""
    lines = ["📋 *DAILY TRAVEL DEALS DIGEST*\n"]
    lines.append(f"_{len(trips)} deals found today_\n")
    lines.append("─" * 20)

    for i, trip in enumerate(trips[:15], 1):  # max 15 in digest
        discount_str = f" (-{trip.discount_pct:.0f}%)" if trip.discount_pct else ""
        deal_icon = "✈️" if trip.deal_type in (DealType.FLIGHT_ONLY, DealType.REPOSITIONED) else "🏨+✈️"
        bc_emoji = _BC_EMOJI.get(trip.booking_confidence, "⚪")
        cat_str = f" | {trip.category.value}" if trip.category else ""
        lines.append(
            f"{i}. {trip.route}\n"
            f"   💰 €{trip.total_cost_eur:.0f}{discount_str} | {deal_icon}{cat_str} | "
            f"{bc_emoji} {trip.booking_confidence.value}"
        )

    lines.append("\n_Reply /deals for full details_")
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


# ── Optional AI enhancement (formatting/summarization only) ──────────────────

async def enhance_with_ai(trip: Trip, base_message: str) -> str:
    """
    Optionally pass the base message through Claude for polish.
    The LLM receives the pre-computed message and MUST NOT change any numbers.
    Falls back to base_message if AI is unavailable or returns garbage.
    """
    from config import get_settings
    settings = get_settings()

    if not settings.anthropic_api_key:
        return base_message

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        system = (
            "You are a travel deal formatter. You receive a pre-formatted Telegram message "
            "and your ONLY job is to slightly improve readability and emoji usage. "
            "NEVER change any price, percentage, date, or airport code. "
            "NEVER add information not present. Return only the improved message text."
        )

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": f"Polish this travel alert (keep all numbers exact):\n\n{base_message}",
                }
            ],
        )

        enhanced = response.content[0].text.strip()

        # Safety check: verify key numbers are preserved
        import re
        original_prices = re.findall(r"€(\d+)", base_message)
        enhanced_prices = re.findall(r"€(\d+)", enhanced)
        if set(original_prices) != set(enhanced_prices):
            log.warning("ai_price_mismatch_fallback", original=original_prices, enhanced=enhanced_prices)
            return base_message

        return enhanced

    except Exception as exc:
        log.warning("ai_enhance_failed", error=str(exc))
        return base_message
