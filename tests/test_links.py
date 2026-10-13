"""Hotels in flight alerts: a search link for the trip dates (B26), the cheapest hotel's price (B48)."""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from ai_layer.formatter import format_flight_only
from api.records import deal_record
from notifier.desktop import deal_toast
from preferences import UserPreferences
from storage.database import DealRow
from storage.models import DealType, FlightLeg, HotelDeal, Trip
from tests.harness import flight, hotel
from utils import airports
from utils.links import booking_search_url, stay_label

GOOGLE = "https://www.google.com/travel/search?q=hotels+in+Krakow&ts=CAEa&hl=en&curr=EUR"


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


# ── The cheapest hotel for the stay (B48) ────────────────────────────────────

def _offer() -> HotelDeal:
    return HotelDeal(name="Antique Apartments", location="Krakow", price_per_night_eur=63.0, nights=3,
                     total_price_eur=189.0, rating=9.0, source="google_hotels", booking_url=GOOGLE,
                     check_in=date(2026, 11, 13), check_out=date(2026, 11, 16))


def test_flight_alert_shows_the_cheapest_hotel_for_its_dates():
    text = format_flight_only(_trip().model_copy(update={"hotel_offer": _offer()}))

    assert '🏨 Hotels in Krakow from €63 a night</a> · €189 for 3 nights at Antique Apartments, 9/10' in text
    assert 'href="https://www.google.com/travel/search?q=hotels+in+Krakow&amp;ts=CAEa' in text
    assert "booking.com" not in text
    assert "<b>€35</b>" in text  # the deal's price is still the fare's


def test_toast_and_deal_record_carry_the_hotel_price():
    trip = _trip().model_copy(update={"hotel_offer": _offer(), "hash": "deal-1"})

    assert "hotels from €63 a night" in deal_toast(trip).message
    record = deal_record(DealRow(trip, "instant", False, "2026-10-13T10:00:00"), UserPreferences(home_airports=["MXP"]))
    assert record["links"]["hotels"] == GOOGLE
    assert (record["hotel_offer"]["price_per_night"], record["hotel_offer"]["total"]) == (63.0, 189.0)
    assert record["hotel"] is None and record["price"] == 35.0


async def test_cycle_prices_a_hotel_for_the_alerted_flight(engine):
    def for_each_stay(tasks):
        return [hotel(name=f"Hotel {t.destination}", location=airports.city_of(t.destination), price_per_night=41.0,
                      days_ahead=(t.depart_from - date.today()).days, nights=t.nights_min,
                      booking_url="https://www.google.com/travel/search?q=x") for t in tasks]

    aggregator = await engine.run_cycle(flights=[flight(destination="KRK", price=25.0, nights=3)], hotels=for_each_stay)

    [tasks] = aggregator._hotel_scrapers[0].calls  # one search, for the flight's own stay
    dep = date.today() + timedelta(days=14)
    assert [(t.destination, t.depart_from, t.return_date) for t in tasks] == [("KRK", dep, dep + timedelta(days=3))]
    [alert] = engine.telegram.texts  # one alert for the fare, its hotel inside
    assert "Hotels in Krakow from €41 a night" in alert and "€123 for 3 nights at Hotel KRK" in alert
