from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from config import get_settings
from scrapers.base import BaseHotelScraper, ScrapeStatus, build_client, random_headers
from storage.models import RawHotelResult, SearchTask
from utils import airports
from utils.iata_extract import parse_amount
from utils.links import BOOKING_SEARCH_URL, booking_search_params
from utils.logging_config import get_logger
from utils.retry import async_retry

log = get_logger(__name__)


_AMOUNT_RE = re.compile(r"\d{1,3}(?:[.,\s\u00a0]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?")


def parse_price_text(text: str) -> Optional[float]:
    """'€ 1,234' → 1234.0 (the old regex read it as 1.23); '€89' → 89.0."""
    m = _AMOUNT_RE.search(text)
    return parse_amount(m.group(0)) if m else None


def is_waf_challenge(html: str) -> bool:
    head = html[:5000].lower()
    return "awswaf" in head or "challenge.js" in head


class BookingComScraper(BaseHotelScraper):
    """
    Hotel scraper using Booking.com's public search interface.

    Falls back to HTML scraping of public search results.
    """

    source_id = "booking_com_api"

    def __init__(self) -> None:
        # Plain-HTTP scraping gets an AWS WAF challenge page; off unless you have a workaround.
        self.enabled = get_settings().enable_booking_html

    def calls_per_cycle(self) -> int:
        return get_settings().booking_calls_per_cycle

    @async_retry(
        max_attempts=3, min_wait=2.0, max_wait=20.0,
        retry_on=(httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError),
    )
    async def _search_html(
        self,
        client: httpx.AsyncClient,
        location: str,
        checkin: date,
        checkout: date,
        adults: int,
    ) -> Optional[str]:
        params = {
            **booking_search_params(location, checkin, checkout, adults),
            "order": "price",
            "nflt": "ht_id=204",  # hotels only (httpx encodes it; it was double-encoded before)
        }
        resp = await client.get(
            BOOKING_SEARCH_URL,
            params=params,
            headers=random_headers({"Referer": "https://www.booking.com/"}),
        )
        resp.raise_for_status()
        return resp.text

    def _parse_html_results(
        self, html: str, location: str, nights: int, checkin: date
    ) -> List[RawHotelResult]:
        soup = BeautifulSoup(html, "lxml")
        results: List[RawHotelResult] = []

        cards = soup.find_all("div", {"data-testid": "property-card"})
        if not cards:
            cards = soup.find_all("div", class_=re.compile(r"sr_property_block|hotel_namecontainer"))

        for card in cards[:20]:
            try:
                name_el = card.find(
                    ["span", "div", "h3"],
                    {"data-testid": "title"}
                ) or card.find(class_=re.compile(r"hotel-name|sr-hotel__name", re.I))
                name = name_el.get_text(strip=True) if name_el else "Unknown"

                price_el = card.find(
                    ["span", "div"],
                    {"data-testid": "price-and-discounted-price"}
                ) or card.find(class_=re.compile(r"prco-inline-block-maker-helper|bui-price-display", re.I))
                price_text = price_el.get_text(" ", strip=True) if price_el else ""
                price = parse_price_text(price_text)
                if not price or price <= 0:
                    continue

                rating_el = card.find(
                    ["div", "span"],
                    {"data-testid": "review-score"}
                )
                rating = None
                if rating_el:
                    rating_m = re.search(r"(\d+(?:[.,]\d+)?)", rating_el.get_text())
                    if rating_m:
                        rating = float(rating_m.group(1).replace(",", "."))

                link_el = card.find("a", href=True)
                booking_url = None
                if link_el:
                    href = link_el["href"]
                    booking_url = href if href.startswith("http") else f"https://www.booking.com{href}"

                results.append(
                    RawHotelResult(
                        name=name,
                        location=location,
                        price_per_night=round(price / nights, 2),
                        currency="EUR",
                        nights=nights,
                        rating=rating,
                        booking_url=booking_url,
                        source=self.source_id,
                        check_in=checkin,
                        check_out=checkin + timedelta(days=nights),
                    )
                )
            except Exception as exc:
                log.debug("booking_card_parse_error", error=str(exc))

        return results

    async def scrape(self, tasks: List[SearchTask]) -> List[RawHotelResult]:
        results: List[RawHotelResult] = []
        async with build_client(timeout=25.0) as client:
            for task in tasks:
                if not task.destination:
                    continue
                location = airports.city_of(task.destination)
                nights = task.nights_min or 3
                checkin, checkout = task.depart_from, task.return_date
                try:
                    html = await self._search_html(client, location, checkin, checkout, task.adults)
                    if html and is_waf_challenge(html):
                        self._report_status(ScrapeStatus.BLOCKED, "AWS WAF challenge page")
                        break
                    if html:
                        found = self._parse_html_results(html, location, nights, checkin)
                        results.extend(found)
                    await asyncio.sleep(1.0)
                except Exception as exc:
                    log.warning("booking_search_failed", location=location, error=str(exc))
                    self._record_error(f"{location}: {exc}")
        return results
