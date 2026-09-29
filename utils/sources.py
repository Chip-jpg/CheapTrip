"""How each source's name reads in messages (the engine's ids: google_flights → Google Flights)."""
from __future__ import annotations

SOURCE_NAMES = {
    "ryanair": "Ryanair",
    "google_flights": "Google Flights",
    "travelpayouts": "Travelpayouts",
    "skyscanner_api": "Skyscanner",
    "piratinviaggio": "PiratinViaggio",
    "piratinviaggio_hotels": "PiratinViaggio hotels",
    "secret_flying": "Secret Flying",
    "going": "Going",
    "holiday_pirates": "HolidayPirates",
    "holiday_pirates_hotels": "HolidayPirates hotels",
    "booking_com_api": "Booking.com",
    "google_hotels": "Google Hotels",
}


def source_name(source_id: str) -> str:
    return SOURCE_NAMES.get(source_id, source_id.replace("_", " ").title())
