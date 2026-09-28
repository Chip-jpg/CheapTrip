"""Hotel search link in flight alerts (B26)."""
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

from ai_layer.formatter import format_flight_only
from storage.models import DealType, FlightLeg, Trip
from tests.harness import flight
from utils.links import booking_search_url, stay_label


def _trip(dep=date(2026, 11, 13), ret=date(2026, 11, 16), dest="KRK") -> Trip:
    leg = FlightLeg(origin="MXP", destination=dest, price_eur=35.0, departure_date=dep, return_date=ret,
                    source="ryanair", booking_url="https://www.ryanair.com/x?a=1&b=2")
    return Trip(deal_type=DealType.FLIGHT_ONLY, route="Milan → Krakow", outbound_flight=leg,
                flight_cost_eur=35.0, total_cost_eur=35.0, departure_date=dep, return_date=ret)


def test_booking_url_carries_city_dates_and_currency():
    url = booking_search_url("Krakow", date(2026, 11, 13), date(2026, 11, 16), adults=2)

    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://www.booking.com/searchresults.html?")
    assert q["ss"] == "Krakow"
    assert (q["checkin_year"], q["checkin_month"], q["checkin_monthday"]) == ("2026", "11", "13")
    assert (q["checkout_year"], q["checkout_month"], q["checkout_monthday"]) == ("2026", "11", "16")
    assert q["group_adults"] == "2" and q["selected_currency"] == "EUR"


def test_stay_label():
    assert stay_label(date(2026, 11, 13), date(2026, 11, 16)) == "13–16 Nov"
    assert stay_label(date(2026, 10, 30), date(2026, 11, 2)) == "30 Oct–2 Nov"


def test_flight_alert_links_hotels_for_the_trip_dates():
    text = format_flight_only(_trip())

    assert "🏨 Hotels in Krakow · 13–16 Nov" in text
    assert "booking.com/searchresults.html?ss=Krakow&amp;checkin_year=2026" in text  # escaped for HTML


def test_london_airports_link_the_city_not_the_airport():
    assert "Hotels in London" in format_flight_only(_trip(dest="STN"))


def test_no_hotel_link_without_a_return_date():
    assert "Hotels in" not in format_flight_only(_trip(ret=None))
    assert "Hotels in" not in format_flight_only(_trip(dep=None, ret=None))


async def test_instant_alert_carries_the_hotel_link(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=25.0, nights=3)])

    assert "booking.com/searchresults.html" in engine.telegram.texts[0]
    assert "Hotels in Krakow" in engine.telegram.texts[0]
