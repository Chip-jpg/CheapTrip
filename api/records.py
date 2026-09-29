"""
UI-ready records (B38): flat JSON the app's screens render directly, built from
the engine's models. Dates are ISO strings, prices are euros per person.
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from preferences import UserPreferences
from storage.database import DealRow
from storage.models import DealType, HotelDeal, Trip
from utils import airports
from utils.links import booking_search_url

TRIP_LENGTHS = {"weekend": (2, 4), "short": (4, 7), "medium": (7, 14), "long": (14, 30)}


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def place(code: Optional[str]) -> Optional[Dict[str, Any]]:
    """An airport (or metropolitan area) as {code, city, country}."""
    if not code:
        return None
    airport = airports.get(code)
    return {"code": code, "city": airports.display_name(code), "country": airport.country if airport else None}


def deal_type(trip: Trip) -> str:
    """flight / one_way / package / hotel / flight_hotel / via_hub (error fares keep their flight type)."""
    kind = DealType(trip.deal_type)
    if kind == DealType.PACKAGE:
        return "package"
    if kind == DealType.HOTEL_ONLY:
        return "hotel"
    if kind == DealType.COMPLETE_TRIP:
        return "flight_hotel"
    if kind == DealType.REPOSITIONED:
        return "via_hub"
    leg = trip.outbound_flight
    return "flight" if trip.return_date or (leg and leg.is_round_trip) else "one_way"


def _destination_code(trip: Trip) -> Optional[str]:
    if trip.outbound_flight:
        return trip.outbound_flight.destination
    if trip.hotel:
        return airports.iata_for_city(trip.hotel.location.split(",")[0])
    return None


def _hotel(hotel: HotelDeal) -> Dict[str, Any]:
    return {
        "name": hotel.name, "location": hotel.location,
        "price_per_night": hotel.price_per_night_eur, "nights": hotel.nights, "total": hotel.total_price_eur,
        "price_basis": hotel.price_basis, "rating": hotel.rating, "review_count": hotel.review_count,
        "stars": hotel.stars, "check_in": _iso(hotel.check_in), "check_out": _iso(hotel.check_out),
        "travel_window": hotel.travel_window, "booking_url": hotel.booking_url,
    }


def _legs(trip: Trip) -> List[Dict[str, Any]]:
    """Both parts of a trip via a hub: home → hub, then hub → destination."""
    legs = [
        {"from": place(r.origin), "to": place(r.hub), "price": r.price_eur, "depart_date": _iso(r.departure_date),
         "return_date": _iso(r.return_date), "booking_url": r.booking_url, "source": r.source}
        for r in trip.repositioning_legs
    ]
    leg = trip.outbound_flight
    if leg:
        legs.append({"from": place(leg.origin), "to": place(leg.destination), "price": leg.price_eur,
                     "depart_date": _iso(leg.departure_date), "return_date": _iso(leg.return_date),
                     "booking_url": leg.booking_url, "source": leg.source})
    return legs


def _hotel_search(trip: Trip, city: Optional[str], adults: int) -> Optional[str]:
    if not (city and trip.departure_date and trip.return_date and trip.return_date > trip.departure_date):
        return None
    return booking_search_url(city, trip.departure_date, trip.return_date, adults=adults)


def deal_record(
    row: DealRow, prefs: UserPreferences, notified: Optional[Tuple[str, str]] = None,
) -> Dict[str, Any]:
    """Everything a deal card and the deal detail panel show."""
    trip = row.trip
    leg = trip.outbound_flight
    dest = _destination_code(trip)
    destination = place(dest)
    city = destination["city"] if destination else None
    record: Dict[str, Any] = {
        "id": trip.hash,
        "type": deal_type(trip),
        "tier": row.alert_tier,
        "route": trip.route,
        "origin": place(leg.origin) if leg else None,
        "destination": destination,
        "depart_date": _iso(trip.departure_date),
        "return_date": _iso(trip.return_date),
        "nights": trip.nights,
        "length": trip.trip_length_profile.value if trip.trip_length_profile else None,
        "travel_window": (leg.travel_window if leg else None) or (trip.hotel.travel_window if trip.hotel else None),
        "price": trip.total_cost_eur,
        "usual_price": trip.normal_price_eur,
        "discount_pct": trip.discount_pct,
        "previous_price": trip.previous_price_eur,
        "reasons": list(trip.alert_reasons),
        "sources": list(trip.source_list),
        "confidence": str(trip.booking_confidence.value if hasattr(trip.booking_confidence, "value")
                          else trip.booking_confidence).lower(),
        "is_error_fare": trip.is_error_fare,
        "is_priority": bool(dest) and dest in prefs.priority_destinations,
        "is_muted": bool(dest) and dest in prefs.excluded_destinations,
        "flight": None,
        "hotel": _hotel(trip.hotel) if trip.hotel else None,
        "package_hotel": leg.package_hotel if leg and leg.is_package else None,
        "legs": _legs(trip) if DealType(trip.deal_type) == DealType.REPOSITIONED else [],
        "links": {
            "book": (leg.booking_url if leg else None) or (trip.hotel.booking_url if trip.hotel else None),
            "hotels": _hotel_search(trip, city, prefs.adults) if leg else None,
        },
        "notified": {"at": notified[0], "channels": notified[1].split(",")} if notified else None,
        "waiting": row.alert_tier == "instant" and not row.is_alerted,
        "found_at": row.created_at,
    }
    if leg:
        record["flight"] = {
            "airline": leg.airline, "departure_time": leg.departure_time.isoformat() if leg.departure_time else None,
            "arrival_time": leg.arrival_time.isoformat() if leg.arrival_time else None, "stops": leg.stops,
            "duration_minutes": leg.duration_minutes, "round_trip": leg.is_round_trip,
        }
    return record


def matches(record: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    """The Deals screen's filters: types, lengths, max_price, date_from/date_to, q (destination), priority_only."""
    if filters.get("types") and not (
        record["type"] in filters["types"] or ("error_fare" in filters["types"] and record["is_error_fare"])
    ):
        return False
    if filters.get("lengths") and record["length"] not in filters["lengths"]:
        return False
    if filters.get("max_price") is not None and record["price"] > filters["max_price"]:
        return False
    depart = record["depart_date"]
    if filters.get("date_from") and (not depart or depart < filters["date_from"]):
        return False
    if filters.get("date_to") and (not depart or depart > filters["date_to"]):
        return False
    if filters.get("priority_only") and not record["is_priority"]:
        return False
    query = (filters.get("q") or "").strip().casefold()
    if query:
        dest = record["destination"] or {}
        haystack = " ".join(str(x) for x in (dest.get("code"), dest.get("city"), record["route"]) if x).casefold()
        if query not in haystack:
            return False
    return True


def destination_rows(fares: Sequence[Tuple[str, str, str, float, str]]) -> Dict[str, Dict[str, Any]]:
    """
    The Destinations screen's "All seen": per destination airport, the best
    fare (its route and dates), the usual price (median of each fare's latest
    price), how many different fares, when it was last seen, and the trend:
    the cheapest fare seen each day.
    """
    latest: Dict[str, Dict[str, Tuple[float, str, str, str, str]]] = {}
    daily: Dict[str, Dict[str, float]] = {}
    for route, depart, ret, price, recorded_at in fares:
        dest = route.split("-", 1)[1] if "-" in route else route
        latest.setdefault(dest, {})[f"{route} {depart} {ret}"] = (price, depart, recorded_at, route, ret)
        day = str(recorded_at)[:10]
        days = daily.setdefault(dest, {})
        days[day] = min(price, days.get(day, price))
    rows = {}
    for dest, by_fare in latest.items():
        prices = [fare[0] for fare in by_fare.values()]
        best_price, best_depart, _, best_route, best_return = min(by_fare.values())
        rows[dest] = {
            **place(dest),
            "best_price": best_price,
            "best_depart": best_depart,
            "best_return": best_return or None,
            "best_route": best_route,
            "usual_price": round(statistics.median(prices), 2),
            "fares": len(by_fare),
            "last_seen": max(fare[2] for fare in by_fare.values()),
            "trend": [{"day": day, "price": price} for day, price in sorted(daily[dest].items())],
        }
    return rows
