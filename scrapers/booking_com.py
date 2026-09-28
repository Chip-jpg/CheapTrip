from __future__ import annotations

import asyncio
import os
import re
from datetime import date, timedelta
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseHotelScraper, build_client, random_headers
from storage.models import RawHotelResult, ScraperParams
from utils import airports
from utils.logging_config import get_logger
from utils.retry import async_retry

log = get_logger(__name__)

_SEARCH_BASE = "https://www.booking.com/searchresults.html"


class BookingComScraper(BaseHotelScraper):
    """
    Hotel scraper using Booking.com's public search interface.

    Falls back to HTML scraping of public search results.
    """

    source_id = "booking_com_api"

    def __init__(self) -> None:
        self._affiliate_key = os.getenv("BOOKING_COM_API_KEY", "")
        self.enabled = True

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
            "ss": location,
            "checkin_year": str(checkin.year),
            "checkin_month": str(checkin.month),
            "checkin_monthday": str(checkin.day),
            "checkout_year": str(checkout.year),
            "checkout_month": str(checkout.month),
            "checkout_monthday": str(checkout.day),
            "group_adults": str(adults),
            "no_rooms": "1",
            "order": "price",
            "nflt": "ht_id%3D204",
            "lang": "en-gb",
            "selected_currency": "EUR",
        }
        resp = await client.get(
            _SEARCH_BASE,
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
                price_text = price_el.get_text(strip=True) if price_el else ""
                price_m = re.search(r"(\d{2,4}(?:[.,]\d{2})?)", price_text.replace(" ", ""))
                if not price_m:
                    continue
                price = float(price_m.group(1).replace(",", "."))
                if price <= 0:
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

    async def scrape(self, params: ScraperParams) -> List[RawHotelResult]:
        results: List[RawHotelResult] = []
        nights = params.nights_min or 3
        checkin = params.departure_date_from
        checkout = checkin + timedelta(days=nights)

        locations = []
        for dest in params.destinations[:5]:
            city = airports.city_of(dest)
            locations.append(city)

        async with build_client(timeout=25.0) as client:
            for location in locations:
                try:
                    html = await self._search_html(
                        client, location, checkin, checkout, params.adults
                    )
                    if html:
                        found = self._parse_html_results(html, location, nights, checkin)
                        results.extend(found)
                    await asyncio.sleep(1.0)
                except Exception as exc:
                    log.warning("booking_search_failed", location=location, error=str(exc))
        return results
