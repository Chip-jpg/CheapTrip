"""Search links the user can open (hotel searches pre-filled with a trip's city and dates)."""
from __future__ import annotations

from datetime import date
from typing import Dict
from urllib.parse import urlencode

BOOKING_SEARCH_URL = "https://www.booking.com/searchresults.html"


def booking_search_params(city: str, check_in: date, check_out: date, adults: int = 1) -> Dict[str, str]:
    """Booking.com search query for a stay (also used by the Booking scraper)."""
    return {
        "ss": city,
        "checkin_year": str(check_in.year),
        "checkin_month": str(check_in.month),
        "checkin_monthday": str(check_in.day),
        "checkout_year": str(check_out.year),
        "checkout_month": str(check_out.month),
        "checkout_monthday": str(check_out.day),
        "group_adults": str(adults),
        "no_rooms": "1",
        "lang": "en-gb",
        "selected_currency": "EUR",
    }


def booking_search_url(city: str, check_in: date, check_out: date, adults: int = 1) -> str:
    return f"{BOOKING_SEARCH_URL}?{urlencode(booking_search_params(city, check_in, check_out, adults))}"


def stay_label(check_in: date, check_out: date) -> str:
    """'6–8 Nov', or '30 Oct–2 Nov' across months."""
    if check_in.month == check_out.month:
        return f"{check_in.day}–{check_out.day} {check_out.strftime('%b')}"
    return f"{check_in.day} {check_in.strftime('%b')}–{check_out.day} {check_out.strftime('%b')}"
