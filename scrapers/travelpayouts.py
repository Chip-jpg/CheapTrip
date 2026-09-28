"""
Travelpayouts / Aviasales Data API — cached cheapest prices.

  GET https://api.travelpayouts.com/aviasales/v3/prices_for_dates
      ?origin=MIL&departure_at=2026-10&one_way=false&sorting=price&unique=true
       &currency=eur&limit=1000            (header X-Access-Token: <token>)

Without a destination the API returns the cheapest round trip per
destination from the origin city for that departure month — a good
"anywhere" discovery source. Prices come from searches other users made
recently, so they are flagged price_is_cached and weighted lower.

Response rows (per the API docs):
  {"origin": "MIL", "destination": "KRK", "origin_airport": "BGY",
   "destination_airport": "KRK", "price": 38, "airline": "FR",
   "flight_number": "3408", "departure_at": "2026-10-15T06:30:00+02:00",
   "return_at": "2026-10-19T21:05:00+02:00", "transfers": 0,
   "return_transfers": 0, "duration": 230, "link": "/search/MIL1510KRK19101"}
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import get_settings
from scrapers.base import BaseFlightScraper, ScrapeStatus, SearchCapability, build_client
from storage.models import RawFlightResult, SearchTask
from utils.airport_clusters import cluster_city_code
from utils.logging_config import get_logger

log = get_logger(__name__)

_API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
_SITE = "https://www.aviasales.com"

# Airline names for common IATA carrier codes (others are shown as the code)
_AIRLINES = {
    "FR": "Ryanair", "U2": "easyJet", "W6": "Wizz Air", "W4": "Wizz Air Malta", "VY": "Vueling",
    "AZ": "ITA Airways", "LH": "Lufthansa", "AF": "Air France", "KL": "KLM", "BA": "British Airways",
    "IB": "Iberia", "TP": "TAP Air Portugal", "LX": "SWISS", "OS": "Austrian", "SK": "SAS",
    "TK": "Turkish Airlines", "EK": "Emirates", "QR": "Qatar Airways", "EY": "Etihad",
    "DL": "Delta", "UA": "United", "AA": "American Airlines", "V7": "Volotea", "EW": "Eurowings",
}


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


class TravelpayoutsScraper(BaseFlightScraper):
    source_id = "travelpayouts"
    capability = SearchCapability.ANYWHERE

    def __init__(self) -> None:
        settings = get_settings()
        self._token = settings.travelpayouts_token
        self._marker = settings.travelpayouts_marker
        self.enabled = bool(self._token)

    def calls_per_cycle(self) -> int:
        return get_settings().travelpayouts_calls_per_cycle

    @staticmethod
    def query_for(task: SearchTask) -> Tuple[str, Optional[str], str]:
        """(origin city/airport, destination, departure month) — several tasks can share one query."""
        origin = cluster_city_code(task.origin) or task.origin
        return origin, task.destination, task.depart_from.strftime("%Y-%m")

    def _booking_url(self, link: Optional[str]) -> Optional[str]:
        if not link:
            return None
        url = link if link.startswith("http") else f"{_SITE}{link}"
        if self._marker:
            url += ("&" if "?" in url else "?") + f"marker={self._marker}"
        return url

    def parse_rows(self, rows: List[Dict[str, Any]], task: SearchTask) -> List[RawFlightResult]:
        """Rows → results, keeping only departures and stay lengths the task asked for."""
        results: List[RawFlightResult] = []
        for row in rows:
            try:
                dep, ret = _parse_dt(row.get("departure_at")), _parse_dt(row.get("return_at"))
                if not dep or not ret:
                    continue
                nights = (ret.date() - dep.date()).days
                if not (task.depart_from <= dep.date() <= task.depart_to):
                    continue
                if not (task.nights_min <= nights <= task.nights_max):
                    continue
                price = float(row["price"])
                if price <= 0:
                    continue
                airline_code = row.get("airline")
                stops = row.get("transfers")
                results.append(RawFlightResult(
                    origin=row.get("origin_airport") or row["origin"],
                    destination=row.get("destination_airport") or row["destination"],
                    price=price,
                    currency="EUR",
                    departure_date=dep.date(),
                    return_date=ret.date(),
                    airline=_AIRLINES.get(airline_code, airline_code),
                    booking_url=self._booking_url(row.get("link")),
                    source=self.source_id,
                    departure_time=dep.replace(tzinfo=None),
                    flight_number=f"{airline_code}{row['flight_number']}" if airline_code and row.get("flight_number") else None,
                    stops=int(stops) if stops is not None else None,
                    duration_minutes=row.get("duration_to") or None,
                    is_round_trip=True,
                    price_is_cached=True,
                ))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("travelpayouts_parse_error", error=str(exc))
        return results

    async def _fetch(self, client: httpx.AsyncClient, query: Tuple[str, Optional[str], str]) -> Optional[List[dict]]:
        origin, destination, month = query
        params = {
            "origin": origin, "departure_at": month, "one_way": "false", "direct": "false",
            "sorting": "price", "unique": "true", "currency": "eur", "limit": 1000,
        }
        if destination:
            params["destination"] = destination
        resp = await client.get(_API, params=params, headers={"X-Access-Token": self._token})
        if resp.status_code == 401:
            self._report_status(ScrapeStatus.ERROR, "invalid Travelpayouts token (HTTP 401)")
            return None
        if resp.status_code == 429:
            self._report_status(ScrapeStatus.RATE_LIMITED, "HTTP 429")
            return None
        if resp.status_code != 200:
            self._record_error(f"{origin} {month}: HTTP {resp.status_code}")
            return []
        body = resp.json()
        if not body.get("success", True):
            self._record_error(f"{origin} {month}: {str(body.get('error'))[:100]}")
            return []
        return body.get("data") or []

    async def scrape(self, tasks: List[SearchTask]) -> List[RawFlightResult]:
        results: List[RawFlightResult] = []
        cache: Dict[Tuple[str, Optional[str], str], List[dict]] = {}
        async with build_client(timeout=25.0) as client:
            for task in tasks:
                if not task.origin:
                    continue
                query = self.query_for(task)
                if query not in cache:
                    try:
                        rows = await self._fetch(client, query)
                    except (httpx.HTTPError, ValueError) as exc:
                        self._record_error(f"{query[0]} {query[2]}: {exc}")
                        rows = []
                    if rows is None:  # auth failure or rate limit: stop this cycle
                        break
                    cache[query] = rows
                results.extend(self.parse_rows(cache[query], task))
        # Overlapping tasks (e.g. weekend and short stays) can select the same row
        unique = {(r.origin, r.destination, r.departure_date, r.return_date, r.price): r for r in results}
        return list(unique.values())
