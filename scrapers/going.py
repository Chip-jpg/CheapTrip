from __future__ import annotations

from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from config import get_settings
from scrapers.base import BaseFeedScraper, build_client, random_headers
from storage.models import RawFlightResult
from utils.iata_extract import extract_price, extract_route, extract_travel_window
from utils.logging_config import get_logger
from utils.retry import async_retry

log = get_logger(__name__)

_BASE = "https://going.com"
_DEALS_URL = f"{_BASE}/deals"


class GoingScraper(BaseFeedScraper):
    """Scrapes Going (formerly Scott's Cheap Flights) public deal feed.

    Disabled by default — site is a JavaScript SPA with no server-rendered content.
    Set ENABLE_GOING=true in .env to attempt anyway.
    """

    source_id = "going"

    def __init__(self) -> None:
        self.enabled = get_settings().enable_going
        if not self.enabled:
            log.info(
                "scraper_disabled",
                source=self.source_id,
                reason="JavaScript SPA — deals are loaded client-side, not via HTTP",
            )

    @async_retry(
        max_attempts=3, min_wait=2.0, max_wait=15.0,
        retry_on=(httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError),
    )
    async def _fetch(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        resp = await client.get(url, headers=random_headers())
        resp.raise_for_status()
        return resp.text

    def _parse_card(self, card: BeautifulSoup) -> Optional[RawFlightResult]:
        text = card.get_text(" ", strip=True)
        route = extract_route(text)
        price = extract_price(text)
        if not route or not price or not (5 <= price[0] <= 3000):
            return None

        link = card.find("a", href=True)
        booking_url = None
        if link:
            href = link["href"]
            booking_url = href if href.startswith("http") else f"{_BASE}{href}"

        return RawFlightResult(
            origin=route[0],
            destination=route[1],
            price=price[0],
            currency=price[1],
            departure_date=None,
            travel_window=extract_travel_window(text),
            booking_url=booking_url,
            source=self.source_id,
        )

    def _parse_page(self, html: str) -> List[RawFlightResult]:
        soup = BeautifulSoup(html, "lxml")
        results = []
        for card in soup.find_all("article"):
            r = self._parse_card(card)
            if r:
                results.append(r)
        return results

    async def scrape_feed(self) -> List[RawFlightResult]:
        async with build_client(timeout=20.0) as client:
            try:
                html = await self._fetch(client, _DEALS_URL)
            except Exception as exc:
                log.warning("going_scrape_failed", error=str(exc))
                self._record_error(str(exc))
                return []
        return self._parse_page(html) if html else []
