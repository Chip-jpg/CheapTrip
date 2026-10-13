"""Trip assembly (B15): stay length, profiles, home origins and date-matched hotels."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from normalizers.hotel import normalize_hotels
from storage.database import init_db
from storage.models import DealType, FlightLeg, HotelDeal, TripLengthProfile
from tests.harness import flight, hotel
from trip_builder.builder import build_trips

DEP = date.today() + timedelta(days=21)


def _leg(origin: str = "MXP", dest: str = "KRK", nights: Optional[int] = 3, price: float = 40.0) -> FlightLeg:
    return FlightLeg(
        origin=origin, destination=dest, price_eur=price, departure_date=DEP,
        return_date=DEP + timedelta(days=nights) if nights else None,
        source="ryanair", booking_url="https://example.com/book", is_round_trip=bool(nights),
    )


def _hotel(check_in: date = DEP, nights: int = 3, location: str = "Krakow", name: str = "Hotel Test",
           per_night: float = 30.0) -> HotelDeal:
    return HotelDeal(
        name=name, location=location, price_per_night_eur=per_night, nights=nights,
        total_price_eur=per_night * nights, rating=8.5, source="fake_hotels",
        check_in=check_in, check_out=check_in + timedelta(days=nights),
    )


async def test_round_trip_gets_its_stay_length_and_profile(engine):
    await init_db()
    [trip] = await build_trips([_leg(nights=3)], [])

    assert trip.nights == 3
    assert trip.trip_length_profile == TripLengthProfile.WEEKEND


async def test_one_way_fare_has_no_length_or_profile(engine):
    await init_db()
    [trip] = await build_trips([_leg(nights=None)], [])

    assert trip.nights is None
    assert trip.trip_length_profile is None
    assert trip.is_feasible


async def test_long_haul_ten_nights_is_medium_and_feasible(engine):
    # Before B15 flight-only trips had no nights → SHORT (12h cap) → New York marked infeasible
    await init_db()
    [trip] = await build_trips([_leg(dest="JFK", nights=10, price=380.0)], [])

    assert trip.nights == 10
    assert trip.trip_length_profile == TripLengthProfile.MEDIUM
    assert trip.is_feasible, trip.feasibility_notes


async def test_non_home_origin_is_not_a_trip(engine):
    await init_db()
    trips = await build_trips([_leg(origin="FCO")], [])

    assert trips == []


async def test_cheapest_hotel_for_the_flight_dates_is_shown_with_the_flight(engine):
    # B48: one deal per fare, with its hotel; pairing every hotel as its own deal alerted up to four times
    await init_db()
    cheap = _hotel(check_in=DEP, nights=3, name="Cheap", per_night=25.0)
    trips = await build_trips([_leg(nights=3)], [_hotel(check_in=DEP, nights=3), cheap])

    [trip] = trips
    assert trip.deal_type == DealType.FLIGHT_ONLY
    assert trip.hotel_offer.name == "Cheap" and trip.hotel_offer.total_price_eur == 75.0
    assert trip.total_cost_eur == 40.0  # judged on the fare alone
    assert trip.hotel is None


async def test_hotel_on_other_dates_is_not_offered(engine):
    await init_db()
    trips = await build_trips(
        [_leg(nights=3)],
        [_hotel(check_in=DEP + timedelta(days=7), nights=3), _hotel(check_in=DEP, nights=5)],
    )

    assert [(t.deal_type, t.hotel_offer) for t in trips] == [(DealType.FLIGHT_ONLY, None)]


async def test_hotel_below_the_quality_bar_is_not_offered(engine):
    await init_db()
    poor = _hotel(check_in=DEP, nights=3).model_copy(update={"meets_quality_threshold": False})
    [trip] = await build_trips([_leg(nights=3)], [poor])

    assert trip.hotel_offer is None


async def test_hotel_with_too_few_reviews_is_not_offered(engine):
    engine.set_prefs(min_hotel_review_count=100)
    await init_db()
    few = _hotel(check_in=DEP, nights=3).model_copy(update={"review_count": 12})
    [trip] = await build_trips([_leg(nights=3)], [few])

    assert trip.hotel_offer is None


async def test_hotel_in_a_cluster_mate_city_is_offered(engine):
    # A Stansted fare gets a hotel "in London" (its city's airports include LGW, LHR...)
    await init_db()
    [trip] = await build_trips([_leg(dest="STN", nights=3)], [_hotel(check_in=DEP, nights=3, location="London")])

    assert trip.hotel_offer is not None


async def test_home_airports_come_from_preferences(engine):
    engine.set_prefs(home_airports=["TRN"])
    await engine.run_cycle(flights=[flight(origin="TRN", destination="KRK", price=25.0)])

    assert len(engine.telegram.texts) == 1
    assert "Turin → Krakow" in engine.telegram.texts[0]


async def test_alert_shows_the_stay_length(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=25.0, nights=3)])

    assert "(3 nights)" in engine.telegram.texts[0]


async def test_same_hotel_on_different_dates_stays_two_offers(engine):
    deals = await normalize_hotels([
        hotel(days_ahead=14, nights=3, price_per_night=30.0),
        hotel(days_ahead=21, nights=3, price_per_night=20.0),
    ])

    # Grouping by name+location only used to keep just the cheaper stay a week later
    assert sorted((d.check_in - date.today()).days for d in deals) == [14, 21]
