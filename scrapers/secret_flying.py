from __future__ import annotations

from typing import List, Optional, Set

import httpx
from bs4 import BeautifulSoup

from config import get_settings
from preferences import get_preferences
from scrapers.base import BaseFeedScraper, build_client, random_headers
from storage.models import RawFlightResult
from utils import airports
from utils.airport_clusters import expand_list_to_clusters
from utils.iata_extract import extract_price, extract_route, extract_travel_window
from utils.logging_config import get_logger
from utils.retry import async_retry

log = get_logger(__name__)

_BASE_URL = "https://www.secretflying.com"
_ERROR_FARE_PATH = "/posts/category/error-fares"
_DEALS_PATHS = [
    "/posts/category/europe",
    "/posts/category/us-deals",
    _ERROR_FARE_PATH,
    "/posts/",
]


def home_countries() -> Set[str]:
    """Countries of the user's home airports (deals from/to them are relevant)."""
    codes = expand_list_to_clusters(get_preferences().home_airports)
    return {a.country for a in (airports.get(c) for c in codes) if a}


def _country(code: str) -> Optional[str]:
    airport = airports.get(code)
    return airport.country if airport else None


class SecretFlyingScraper(BaseFeedScraper):
    """Scrapes Secret Flying deal posts via HTML parsing.

    Disabled by default — site is behind Cloudflare protection.
    Set ENABLE_SECRET_FLYING=true in .env to attempt anyway (e.g. if using a proxy).
    """

    source_id = "secret_flying"

    def __init__(self) -> None:
        self.enabled = get_settings().enable_secret_flying
        self.disabled_reason = None if self.enabled else "behind Cloudflare (ENABLE_SECRET_FLYING=true to try)"
        if not self.enabled:
            log.info(
                "scraper_disabled",
                source=self.source_id,
                reason="Cloudflare protection — set ENABLE_SECRET_FLYING=true to attempt",
            )

    @async_retry(
        max_attempts=3, min_wait=2.0, max_wait=15.0,
        retry_on=(httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError),
    )
    async def _fetch_page(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        resp = await client.get(url, headers=random_headers())
        resp.raise_for_status()
        return resp.text

    def _parse_deal(self, card: BeautifulSoup, is_error_fare_page: bool) -> Optional[RawFlightResult]:
        heading = card.find(["h1", "h2", "h3"])
        title = heading.get_text(" ", strip=True) if heading else ""
        text = card.get_text(" ", strip=True)

        route = extract_route(title) or extract_route(text)
        if not route:
            return None
        price = extract_price(title) or extract_price(text)
        if not price or not (10 <= price[0] <= 3000):
            return None

        link = card.find("a", href=True)
        booking_url = None
        if link:
            href = link["href"]
            booking_url = href if href.startswith("http") else f"{_BASE_URL}{href}"

        return RawFlightResult(
            origin=route[0],
            destination=route[1],
            price=price[0],
            currency=price[1],
            departure_date=None,
            travel_window=extract_travel_window(text),
            is_error_fare_hint=is_error_fare_page or "error fare" in title.lower(),
            booking_url=booking_url,
            source=self.source_id,
        )

    def _parse_page(self, html: str, is_error_fare_page: bool = False) -> List[RawFlightResult]:
        soup = BeautifulSoup(html, "lxml")
        results = []
        for card in soup.find_all("article"):
            r = self._parse_deal(card, is_error_fare_page)
            if r:
                results.append(r)
        return results

    async def scrape_feed(self) -> List[RawFlightResult]:
        results: List[RawFlightResult] = []
        async with build_client(timeout=20.0) as client:
            for path in _DEALS_PATHS:
                try:
                    html = await self._fetch_page(client, f"{_BASE_URL}{path}")
                    if html:
                        results.extend(self._parse_page(html, is_error_fare_page=path == _ERROR_FARE_PATH))
                except Exception as exc:
                    log.warning("sf_page_failed", path=path, error=str(exc))
                    self._record_error(f"{path}: {exc}")

        # Keep deals that start or end in the user's home country
        countries = home_countries()
        relevant = [r for r in results if _country(r.origin) in countries or _country(r.destination) in countries]
        # The same post appears on several category pages; keep the error-fare flag if any copy has it
        unique: dict = {}
        for r in relevant:
            key = (r.origin, r.destination, r.price, r.booking_url)
            if key not in unique or r.is_error_fare_hint:
                unique[key] = r
        return list(unique.values())
