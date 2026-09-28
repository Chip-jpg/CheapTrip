"""Route, price and travel-window extraction from deal-post text."""
from __future__ import annotations

import pytest

from utils.iata_extract import extract_price, extract_route, extract_travel_window, parse_amount


@pytest.mark.parametrize("text", [
    "THE AND EUR FOR BUS",           # THE, FOR and BUS are real IATA codes, but not a route
    "BOOK NOW FOR THE BEST DEALS",
    "Back to School sale",
    "Welcome to Italy",
    "",
])
def test_no_route_from_ordinary_words(text):
    assert extract_route(text) is None


@pytest.mark.parametrize("text,expected", [
    ("MXP – KRK from €19", ("MXP", "KRK")),
    ("BGY-STN return flights", ("BGY", "STN")),
    ("Milan (MXP) to Tokyo (NRT) for €450", ("MXP", "NRT")),
    ("ERROR FARE: Milan, Italy to New York, USA for only €189 roundtrip", ("MXP", "JFK")),
    ("Flights from Milan to Krakow for €25 return", ("MXP", "KRK")),
    ("Rome to Bangkok, Thailand from $399", ("FCO", "BKK")),
    ("Non-stop from Kraków to Paris for €30", ("KRK", "CDG")),
])
def test_routes(text, expected):
    assert extract_route(text) == expected


def test_code_pair_with_unknown_code_is_rejected():
    assert extract_route("ZZQ – KRK from €19") is None


@pytest.mark.parametrize("raw,value", [
    ("1,234", 1234.0), ("1.234", 1234.0), ("12,50", 12.5), ("1,234.56", 1234.56),
    ("1.234,56", 1234.56), ("19.99", 19.99), ("450", 450.0),
])
def test_parse_amount(raw, value):
    assert parse_amount(raw) == value


@pytest.mark.parametrize("text,expected", [
    ("only €189 roundtrip", (189.0, "EUR")),
    ("from £19.99", (19.99, "GBP")),
    ("$450 round trip", (450.0, "USD")),
    ("solo 12,50 € a persona", (12.5, "EUR")),
    ("fares from EUR 1.299", (1299.0, "EUR")),
    ("Milan to NYC for €1,234 in 2026", (1234.0, "EUR")),
])
def test_prices_need_a_currency(text, expected):
    assert extract_price(text) == expected


@pytest.mark.parametrize("text", ["Deals for 2026", "Save 45% today", "Flight 1234"])
def test_bare_numbers_are_not_prices(text):
    assert extract_price(text) is None


def test_travel_window():
    assert extract_travel_window("Travel dates: November – March 2027 on many dates") == "November – March 2027"
    assert extract_travel_window("No dates given") is None
