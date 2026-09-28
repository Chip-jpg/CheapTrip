"""
Google Hotels (no key) — DISABLED BY DEFAULT.

Result cards are server-rendered and parse cleanly: name, nightly and total
price, stay length and dates, rating out of 5 with review count, hotel
class. What does not work yet is choosing the dates: Google ignores
`checkin`/`checkout` URL parameters and dates in the query text and shows
tonight's prices, and the URL encoding it uses for dates (`ts=`) could not
be recovered (see docs/ROADMAP.md, B14).

So the scraper only keeps cards whose own dates match the requested stay.
If Google ignores the dates, it returns nothing and reports BLOCKED
("ignored the requested dates") instead of producing misleading prices.
Enable with ENABLE_GOOGLE_HOTELS=true once dates can be set.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup, Tag

from config import get_settings
from scrapers.base import BaseHotelScraper, ScrapeStatus, SearchCapability, build_client, random_headers
from storage.models import RawHotelResult, SearchTask
from utils import airports
from utils.iata_extract import parse_amount
from utils.logging_config import get_logger

log = get_logger(__name__)

_BASE = "https://www.google.com/travel/search"
_CONSENT_COOKIES = {"SOCS": "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiAo_CmBg", "CONSENT": "YES+cb"}
_REQUEST_SPACING_S = 3.0
_CARD_LEVELS_UP = 4  # price label → card container in the current markup

_PRICE_LABEL_RE = re.compile(r"^Prices starting from €([\d.,]+), (.+)$")
_RATING_RE = re.compile(r"([\d.]+) out of 5 stars from ([\d,]+) reviews?")
_TOTAL_RE = re.compile(r"€([\d.,]+) total")
_NIGHTS_RE = re.compile(r"(\d+) nights? with taxes")
_CLASS_RE = re.compile(r"(\d)-star hotel")
_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
# Google puts thin spaces (U+2009) around the dash: "Sep 28\u2009–\u200929"
_DATES_RE = re.compile(rf"\b({_MONTHS})\s(\d{{1,2}})\s*[–-]\s*(?:({_MONTHS})\s)?(\d{{1,2}})\b")


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)


@dataclass
class HotelCard:
    name: str
    nightly_eur: float
    total_eur: Optional[float]
    nights: Optional[int]
    rating_out_of_5: Optional[float]
    review_count: Optional[int]
    stars: Optional[int]
    dates_text: Optional[str]


def search_url(city: str, check_in: date, check_out: date) -> str:
    return f"{_BASE}?" + urlencode({
        "q": f"hotels in {city}",
        "checkin": check_in.isoformat(), "checkout": check_out.isoformat(),
        "hl": "en", "curr": "EUR",
    })


def _card_container(element: Tag) -> Tag:
    node = element
    for _ in range(_CARD_LEVELS_UP):
        if node.parent is None:
            break
        node = node.parent
    return node


def parse_cards(html: str) -> List[HotelCard]:
    soup = BeautifulSoup(html, "lxml")
    cards: List[HotelCard] = []
    seen = set()
    for element in soup.find_all(attrs={"aria-label": _PRICE_LABEL_RE}):
        m = _PRICE_LABEL_RE.match(element["aria-label"])
        name = m.group(2).strip()
        if name in seen:
            continue
        seen.add(name)
        card = _card_container(element)
        text = card.get_text(" ", strip=True)
        labels = " ".join(e["aria-label"] for e in card.find_all(attrs={"aria-label": True}))
        rating_m = _RATING_RE.search(labels)
        total_m, nights_m = _TOTAL_RE.search(text), _NIGHTS_RE.search(text)
        class_m, dates_m = _CLASS_RE.search(text), _DATES_RE.search(text)
        cards.append(HotelCard(
            name=name,
            nightly_eur=parse_amount(m.group(1)) or 0.0,
            total_eur=parse_amount(total_m.group(1)) if total_m else None,
            nights=int(nights_m.group(1)) if nights_m else None,
            rating_out_of_5=float(rating_m.group(1)) if rating_m else None,
            review_count=int(rating_m.group(2).replace(",", "")) if rating_m else None,
            stars=int(class_m.group(1)) if class_m else None,
            dates_text=dates_m.group(0) if dates_m else None,
        ))
    return cards


def dates_match(dates_text: Optional[str], check_in: date, check_out: date) -> bool:
    """Does a card's 'Nov 6 – 9' / 'Oct 30 – Nov 2' match the requested stay?"""
    if not dates_text:
        return False
    m = _DATES_RE.search(dates_text)
    if not m:
        return False
    in_month, in_day, out_month, out_day = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4))
    return (
        check_in.strftime("%b") == in_month and check_in.day == in_day
        and check_out.strftime("%b") == out_month and check_out.day == out_day
    )


class GoogleHotelsScraper(BaseHotelScraper):
    source_id = "google_hotels"
    capability = SearchCapability.CITY_DATES

    def __init__(self) -> None:
        self.enabled = get_settings().enable_google_hotels

    def calls_per_cycle(self) -> int:
        return get_settings().google_hotels_calls_per_cycle

    def results_for(self, html: str, task: SearchTask, city: str, url: str) -> List[RawHotelResult]:
        check_in, check_out = task.depart_from, task.return_date
        results = []
        for card in parse_cards(html):
            if not dates_match(card.dates_text, check_in, check_out):
                continue
            nights = card.nights or task.nights_min
            per_night = round(card.total_eur / nights, 2) if card.total_eur and nights else card.nightly_eur
            results.append(RawHotelResult(
                name=card.name,
                location=city,
                price_per_night=per_night,
                currency="EUR",
                nights=nights,
                rating=round(card.rating_out_of_5 * 2, 1) if card.rating_out_of_5 else None,
                review_count=card.review_count,
                stars=card.stars,
                booking_url=url,
                source=self.source_id,
                check_in=check_in,
                check_out=check_out,
            ))
        return results

    async def scrape(self, tasks: List[SearchTask]) -> List[RawHotelResult]:
        results: List[RawHotelResult] = []
        async with build_client(timeout=25.0) as client:
            client.cookies.update(_CONSENT_COOKIES)
            for i, task in enumerate(tasks):
                if not task.destination:
                    continue
                if i:
                    await _pause(_REQUEST_SPACING_S)
                city = airports.city_of(task.destination)
                url = search_url(city, task.depart_from, task.return_date)
                try:
                    resp = await client.get(url, headers=random_headers({"Accept-Language": "en-US,en;q=0.9"}))
                except httpx.HTTPError as exc:
                    self._record_error(f"{city}: {exc}")
                    continue
                if resp.status_code == 429:
                    self._report_status(ScrapeStatus.RATE_LIMITED, f"{city}: HTTP 429")
                    break
                if resp.status_code != 200:
                    self._record_error(f"{city}: HTTP {resp.status_code}")
                    continue
                found = self.results_for(resp.text, task, city, url)
                if not found and parse_cards(resp.text):
                    self._report_status(ScrapeStatus.BLOCKED, "Google Hotels ignored the requested dates")
                    break
                results.extend(found)
        return results
