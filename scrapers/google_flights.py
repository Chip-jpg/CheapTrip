"""
Google Flights (no key).

Searches are encoded in the URL as a protobuf (`tfs=`), the same format
the website uses, so the server renders results into the HTML. Each result
card carries an accessibility label with everything we need, e.g.:

  "From 80 euros round trip total. Nonstop flight with Ryanair. Leaves
   Milano Malpensa Airport at 9:15 PM on Friday, November 6 and arrives at
   John Paul II Kraków Balice International Airport at 11:10 PM on Friday,
   November 6. Total duration 1 hr 55 min."

Labels are plain English and far more stable than the page's CSS classes.
EU consent is pre-accepted with cookies; a consent page, a 429 or a
"sorry" page is reported as blocked / rate limited, and the source then
cools down for GOOGLE_FLIGHTS_COOLDOWN_HOURS: hammering Google again next
cycle only extends the block.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from config import get_settings
from scrapers.base import BaseFlightScraper, ScrapeStatus, SearchCapability, build_client, random_headers
from storage.models import RawFlightResult, SearchTask
from utils import protobuf as pb
from utils.logging_config import get_logger
from utils.timeutil import utcnow

log = get_logger(__name__)

_BASE = "https://www.google.com/travel/flights"
# Pre-accepted EU consent (otherwise European IPs get consent.google.com)
_CONSENT_COOKIES = {"SOCS": "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiAo_CmBg", "CONSENT": "YES+cb"}
_REQUEST_SPACING_S = 6.0
# Keep the cheapest few itineraries per search
RESULTS_PER_SEARCH = 3

# Trip enum values in Google's search protobuf
_ROUND_TRIP, _ONE_WAY = 1, 2
_ADULT, _ECONOMY = 1, 1


async def _pause(seconds: float) -> None:
    """Spacing between requests (patched to a no-op in tests)."""
    await asyncio.sleep(seconds)


def _leg(day: date, origin: str, destination: str) -> bytes:
    return (
        pb.field_str(2, day.isoformat())
        + pb.field_bytes(13, pb.field_str(2, origin))
        + pb.field_bytes(14, pb.field_str(2, destination))
    )


def encode_search(origin: str, destination: str, depart: date, return_date: Optional[date], adults: int = 1) -> str:
    """The `tfs` URL parameter for a one-way or round-trip economy search."""
    message = pb.field_bytes(3, _leg(depart, origin, destination))
    if return_date:
        message += pb.field_bytes(3, _leg(return_date, destination, origin))
    for _ in range(max(1, adults)):
        message += pb.field_varint(8, _ADULT)
    message += pb.field_varint(9, _ECONOMY)
    message += pb.field_varint(19, _ROUND_TRIP if return_date else _ONE_WAY)
    return pb.to_url_param(message)


def search_url(origin: str, destination: str, depart: date, return_date: Optional[date], adults: int = 1) -> str:
    tfs = encode_search(origin, destination, depart, return_date, adults)
    return f"{_BASE}?tfs={tfs}&hl=en&curr=EUR"


_SP = r"[\s  ]"
_PRICE_RE = re.compile(rf"From{_SP}+([\d,]+){_SP}+euros?{_SP}+(round trip|one way){_SP}+total", re.I)
_STOPS_RE = re.compile(rf"(Nonstop|(\d+){_SP}+stops?){_SP}+flight{_SP}+with{_SP}+([^.]+)\.")
_TIME = rf"(\d{{1,2}}:\d{{2}}){_SP}*([AP]M)"
_DAY = r"\w+day, (\w+ \d{1,2})"
_LEAVES_RE = re.compile(rf"Leaves (.+?) at {_TIME} on {_DAY} and arrives at (.+?) at {_TIME} on {_DAY}")
_DURATION_RE = re.compile(rf"Total duration (?:(\d+){_SP}*hr)?{_SP}*(?:(\d+){_SP}*min)?")


@dataclass
class ParsedItinerary:
    price: float
    round_trip: bool
    airline: Optional[str]
    stops: Optional[int]
    departure_time: Optional[datetime]
    arrival_time: Optional[datetime]
    duration_minutes: Optional[int]


def _label_datetime(clock: str, meridiem: str, month_day: str, reference: date) -> Optional[datetime]:
    """
    '9:15', 'PM', 'November 6' → datetime. Labels omit the year, so use the
    year that puts the date closest to the search date (a flight leaving on
    31 December can land on 1 January of the next year).
    """
    candidates = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(datetime.strptime(f"{month_day} {year} {clock} {meridiem}", "%B %d %Y %I:%M %p"))
        except ValueError:
            continue
    if not candidates:
        return None
    anchor = datetime.combine(reference, datetime.min.time())
    return min(candidates, key=lambda dt: abs(dt - anchor))


def parse_label(label: str, depart: date) -> Optional[ParsedItinerary]:
    label = label.replace(" ", " ").replace(" ", " ")
    price_m = _PRICE_RE.search(label)
    if not price_m:
        return None
    stops_m = _STOPS_RE.search(label)
    leaves_m = _LEAVES_RE.search(label)
    duration_m = _DURATION_RE.search(label)

    stops = airline = None
    if stops_m:
        stops = 0 if stops_m.group(1) == "Nonstop" else int(stops_m.group(2))
        airline = stops_m.group(3).strip()

    dep_time = arr_time = None
    if leaves_m:
        dep_time = _label_datetime(leaves_m.group(2), leaves_m.group(3), leaves_m.group(4), depart)
        arr_time = _label_datetime(leaves_m.group(6), leaves_m.group(7), leaves_m.group(8), depart)

    duration = None
    if duration_m and (duration_m.group(1) or duration_m.group(2)):
        duration = int(duration_m.group(1) or 0) * 60 + int(duration_m.group(2) or 0)

    return ParsedItinerary(
        price=float(price_m.group(1).replace(",", "")),
        round_trip=price_m.group(2).lower() == "round trip",
        airline=airline,
        stops=stops,
        departure_time=dep_time,
        arrival_time=arr_time,
        duration_minutes=duration,
    )


def _is_consent_or_block_page(resp: httpx.Response, body: str) -> bool:
    host = resp.url.host or ""
    return "consent.google" in host or "/sorry/" in str(resp.url) or "Before you continue" in body[:20000]


class GoogleFlightsScraper(BaseFlightScraper):
    source_id = "google_flights"
    capability = SearchCapability.ROUTE_DATE

    def __init__(self) -> None:
        self._cooldown_until: Optional[datetime] = None

    def calls_per_cycle(self) -> int:
        return get_settings().google_flights_calls_per_cycle

    def begin_cycle(self) -> None:
        if self._cooldown_until and utcnow() >= self._cooldown_until:
            log.info("gf_cooldown_over")
            self._cooldown_until = None
            self.enabled = True
            self.disabled_reason = None

    def _start_cooldown(self) -> None:
        hours = get_settings().google_flights_cooldown_hours
        self._cooldown_until = utcnow() + timedelta(hours=hours)
        self.enabled = False
        self.disabled_reason = f"cooling down until {self._cooldown_until:%H:%M} UTC after a block"
        log.warning("gf_cooldown_started", until=self._cooldown_until.isoformat())

    def parse_results(self, html: str, task: SearchTask) -> List[RawFlightResult]:
        soup = BeautifulSoup(html, "lxml")
        seen = set()
        itineraries: List[ParsedItinerary] = []
        for element in soup.find_all(attrs={"aria-label": True}):
            parsed = parse_label(element["aria-label"], task.depart_from)
            if not parsed:
                continue
            key = (parsed.price, parsed.airline, parsed.departure_time)
            if key not in seen:
                seen.add(key)
                itineraries.append(parsed)

        itineraries.sort(key=lambda it: it.price)
        url = search_url(task.origin, task.destination, task.depart_from, task.return_date, task.adults)
        # The label's "total" is for the whole party (checked live: €42 for 1 adult, €84 for 2)
        party = max(1, task.adults)
        return [
            RawFlightResult(
                origin=task.origin,
                destination=task.destination,
                price=round(it.price / party, 2),
                currency="EUR",
                departure_date=task.depart_from,
                return_date=task.return_date if it.round_trip else None,
                airline=it.airline,
                booking_url=url,
                source=self.source_id,
                departure_time=it.departure_time,
                arrival_time=it.arrival_time,
                stops=it.stops,
                duration_minutes=it.duration_minutes,
                is_round_trip=it.round_trip,
            )
            for it in itineraries[:RESULTS_PER_SEARCH]
        ]

    async def scrape(self, tasks: List[SearchTask]) -> List[RawFlightResult]:
        results: List[RawFlightResult] = []
        async with build_client(timeout=25.0) as client:
            client.cookies.update(_CONSENT_COOKIES)
            for i, task in enumerate(tasks):
                if not task.origin or not task.destination:
                    continue
                if i:
                    await _pause(_REQUEST_SPACING_S)
                route = f"{task.origin}-{task.destination}"
                url = search_url(task.origin, task.destination, task.depart_from, task.return_date, task.adults)
                try:
                    resp = await client.get(url, headers=random_headers({"Accept-Language": "en-US,en;q=0.9"}))
                except httpx.HTTPError as exc:
                    self._record_error(f"{route}: {exc}")
                    continue
                if resp.status_code == 429:
                    self._report_status(ScrapeStatus.RATE_LIMITED, f"{route}: HTTP 429")
                    self._start_cooldown()
                    break
                if resp.status_code != 200:
                    self._record_error(f"{route}: HTTP {resp.status_code}")
                    continue
                if _is_consent_or_block_page(resp, resp.text):
                    self._report_status(ScrapeStatus.BLOCKED, f"{route}: consent or bot-check page")
                    self._start_cooldown()
                    break
                found = self.parse_results(resp.text, task)
                if not found:
                    log.info("gf_no_results", route=route, date=task.depart_from.isoformat())
                results.extend(found)
        return results
