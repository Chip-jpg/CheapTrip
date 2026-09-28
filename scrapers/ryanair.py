"""
Ryanair fare finder (public, no key).

One round-trip request per planned 'anywhere' task returns the cheapest
fare to every Ryanair destination from that airport, for departures in the
task's date range and stays of nights_min–nights_max. That fits the
flexible-date model directly: ~50 destinations per call from BGY.

Endpoint (verified 2026-09):
  GET https://www.ryanair.com/api/farfnd/v4/roundTripFares
      ?departureAirportIataCode=BGY&outboundDepartureDateFrom=…&outboundDepartureDateTo=…
      &inboundDepartureDateFrom=…&inboundDepartureDateTo=…&durationFrom=2&durationTo=4
      &market=it-it&language=en&currency=EUR[&arrivalAirportIataCode=KRK]
The API rejects `limit`; it returns one (cheapest) fare per destination,
and an empty list for airports Ryanair doesn't serve (e.g. LIN).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from config import get_settings
from scrapers.base import BaseFlightScraper, ScrapeStatus, SearchCapability, build_client
from storage.models import RawFlightResult, SearchTask
from utils import airports
from utils.logging_config import get_logger

log = get_logger(__name__)

_API = "https://www.ryanair.com/api/farfnd/v4/roundTripFares"
_REQUEST_SPACING_S = 0.5


async def _pause(seconds: float) -> None:
    """Spacing between requests (patched to a no-op in tests)."""
    await asyncio.sleep(seconds)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def booking_url(market: str, origin: str, dest: str, out_date: str, in_date: str, adults: int = 1) -> str:
    """Deep link to Ryanair's flight selection page. Markets are language-country ("it-it", "en-gb")."""
    language, _, country = market.partition("-")
    query = urlencode({
        "adults": adults, "teens": 0, "children": 0, "infants": 0,
        "dateOut": out_date, "dateIn": in_date, "isReturn": "true",
        "originIata": origin, "destinationIata": dest,
    })
    return f"https://www.ryanair.com/{country or 'gb'}/{language or 'en'}/trip/flights/select?{query}"


class RyanairScraper(BaseFlightScraper):
    source_id = "ryanair"
    capability = SearchCapability.ANYWHERE

    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.enable_ryanair
        self._market = settings.ryanair_market

    def calls_per_cycle(self) -> int:
        return get_settings().ryanair_calls_per_cycle

    def _request_params(self, task: SearchTask) -> Dict[str, Any]:
        params = {
            "departureAirportIataCode": task.origin,
            "outboundDepartureDateFrom": task.depart_from.isoformat(),
            "outboundDepartureDateTo": task.depart_to.isoformat(),
            "inboundDepartureDateFrom": (task.depart_from + timedelta(days=task.nights_min)).isoformat(),
            "inboundDepartureDateTo": (task.depart_to + timedelta(days=task.nights_max)).isoformat(),
            "durationFrom": task.nights_min,
            "durationTo": task.nights_max,
            "market": self._market,
            "language": "en",
            "currency": "EUR",
        }
        if task.destination:
            params["arrivalAirportIataCode"] = task.destination
        return params

    def parse_fares(self, data: Dict[str, Any], adults: int = 1) -> List[RawFlightResult]:
        results: List[RawFlightResult] = []
        for fare in data.get("fares") or []:
            try:
                out, back, summary = fare["outbound"], fare["inbound"], fare["summary"]
                origin = out["departureAirport"]["iataCode"]
                arrival = out["arrivalAirport"]
                dest = arrival["iataCode"]
                if not airports.is_known(dest):
                    city = (arrival.get("city") or {}).get("name") or arrival.get("name")
                    airports.learn(dest, city=city, country=(arrival.get("city") or {}).get("countryCode"),
                                   name=arrival.get("name"))
                price = summary["price"]
                dep_time, ret_time = _parse_dt(out.get("departureDate")), _parse_dt(back.get("departureDate"))
                if not dep_time or not ret_time or float(price["value"]) <= 0:
                    continue
                results.append(RawFlightResult(
                    origin=origin,
                    destination=dest,
                    price=float(price["value"]),
                    currency=price.get("currencyCode", "EUR"),
                    departure_date=dep_time.date(),
                    return_date=ret_time.date(),
                    airline="Ryanair",
                    booking_url=booking_url(
                        self._market, origin, dest, dep_time.date().isoformat(), ret_time.date().isoformat(), adults,
                    ),
                    source=self.source_id,
                    departure_time=dep_time,
                    arrival_time=_parse_dt(out.get("arrivalDate")),
                    flight_number=out.get("flightNumber"),
                    stops=0,
                    is_round_trip=True,
                    extra={
                        "return_flight_number": back.get("flightNumber"),
                        "return_departure_time": back.get("departureDate"),
                        "trip_days": summary.get("tripDurationDays"),
                    },
                ))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("ryanair_parse_error", error=str(exc))
        return results

    async def scrape(self, tasks: List[SearchTask]) -> List[RawFlightResult]:
        results: List[RawFlightResult] = []
        async with build_client(timeout=25.0) as client:
            for i, task in enumerate(tasks):
                if not task.origin:
                    continue
                if i:
                    await _pause(_REQUEST_SPACING_S)
                try:
                    resp = await client.get(_API, params=self._request_params(task), headers={"Accept": "application/json"})
                except httpx.HTTPError as exc:
                    self._record_error(f"{task.origin}: {exc}")
                    continue
                if resp.status_code in (403, 429):
                    # Back off for the rest of this cycle.
                    self._report_status(ScrapeStatus.RATE_LIMITED, f"HTTP {resp.status_code}")
                    break
                if resp.status_code != 200:
                    self._record_error(f"{task.origin}: HTTP {resp.status_code}")
                    continue
                try:
                    results.extend(self.parse_fares(resp.json(), task.adults))
                except ValueError as exc:
                    self._record_error(f"{task.origin}: bad JSON ({exc})")
        return results
