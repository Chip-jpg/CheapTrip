from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from config import get_settings
from scrapers.base import BaseFeedScraper, BaseHotelScraper, SearchCapability, build_client, random_headers
from storage.models import RawFlightResult, RawHotelResult, SearchTask
from utils.iata_extract import extract_price, extract_route, extract_travel_window
from utils.logging_config import get_logger
from utils.retry import async_retry

log = get_logger(__name__)

_BASE = "https://www.holidaypirates.com"

_PRICE_RE = re.compile(r"(?:from\s+)?[€\$£]?\s*(\d{1,4}(?:[.,]\d{2})?)\s*(?:€|EUR|USD|GBP)?", re.I)
# Hotel posts rarely state a stay length; assume a short break.
_DEFAULT_HOTEL_NIGHTS = 3

_DISCOUNT_RE = re.compile(r"-\s*(\d{1,3})\s*%")

_RSS_URLS = [
    f"{_BASE}/feed/",
    f"{_BASE}/rss/",
    f"{_BASE}/feed/atom/",
]

_FLIGHT_DEAL_PATHS = [
    "/deals/flights/",
    "/flight-deals/",
    "/flights/",
    "/deals/",
]

_HOTEL_DEAL_PATHS = [
    "/deals/hotels/",
    "/hotel-deals/",
    "/hotels/",
]


def _first_price(text: str) -> Optional[float]:
    for m in _PRICE_RE.finditer(text):
        try:
            val = float(m.group(1).replace(",", "."))
            if 5 < val < 5000:
                return val
        except ValueError:
            pass
    return None


def _first_discount(text: str) -> Optional[float]:
    m = _DISCOUNT_RE.search(text)
    return float(m.group(1)) if m else None


async def _try_rss_feed(
    client: httpx.AsyncClient,
) -> Tuple[List[str], List[str]]:
    """Try RSS/Atom feeds — these often work even when HTML pages are blocked."""
    flight_urls: List[str] = []
    hotel_urls: List[str] = []

    for feed_url in _RSS_URLS:
        try:
            resp = await client.get(feed_url, headers=random_headers(), follow_redirects=True)
            if resp.status_code != 200:
                continue
            content_type = resp.headers.get("content-type", "")
            text = resp.text

            if "xml" not in content_type and "<rss" not in text[:200] and "<feed" not in text[:200]:
                continue

            soup = BeautifulSoup(text, "lxml-xml") if "xml" in content_type else BeautifulSoup(text, "lxml")
            items = soup.find_all("item") or soup.find_all("entry")

            for item in items:
                link_el = item.find("link")
                link = ""
                if link_el:
                    link = link_el.get("href") or link_el.get_text(strip=True) or ""
                title = item.find("title")
                title_text = title.get_text(strip=True).lower() if title else ""
                combined = f"{title_text} {link}".lower()

                if any(kw in combined for kw in ("flight", "fly", "flug", "voli")):
                    if link and link not in flight_urls:
                        flight_urls.append(link)
                if any(kw in combined for kw in ("hotel", "stay", "accommodation")):
                    if link and link not in hotel_urls:
                        hotel_urls.append(link)

            if flight_urls or hotel_urls:
                log.info("hp_rss_feed_found", url=feed_url, flights=len(flight_urls), hotels=len(hotel_urls))
                break
        except Exception:
            continue

    return flight_urls[:5], hotel_urls[:5]


async def _discover_deal_urls(
    client: httpx.AsyncClient,
) -> Tuple[List[str], List[str]]:
    """Discover deal URLs: try RSS first, then homepage links, then known paths."""
    flight_urls, hotel_urls = await _try_rss_feed(client)
    if flight_urls or hotel_urls:
        return flight_urls, hotel_urls

    try:
        resp = await client.get(_BASE, headers=random_headers(), follow_redirects=True)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            for link in soup.find_all("a", href=True):
                href = link["href"]
                text = (link.get_text(separator=" ") + " " + href).lower()
                full_url = href if href.startswith("http") else urljoin(_BASE, href)
                if _BASE not in full_url:
                    continue
                if any(kw in text for kw in ("flight", "fly", "flug", "voli")):
                    if full_url not in flight_urls:
                        flight_urls.append(full_url)
                if any(kw in text for kw in ("hotel", "stay", "accommodation")):
                    if full_url not in hotel_urls:
                        hotel_urls.append(full_url)
    except Exception as exc:
        log.debug("hp_homepage_failed", error=str(exc))

    if flight_urls or hotel_urls:
        return flight_urls[:5], hotel_urls[:5]

    for path in _FLIGHT_DEAL_PATHS:
        flight_urls.append(f"{_BASE}{path}")
    for path in _HOTEL_DEAL_PATHS:
        hotel_urls.append(f"{_BASE}{path}")

    return flight_urls, hotel_urls


class HolidayPiratesFlightScraper(BaseFeedScraper):
    """Scrapes HolidayPirates flight deals (once per cycle).

    Tries RSS feed first (bypasses bot protection), then homepage discovery,
    then known deal paths as fallback.
    """

    source_id = "holiday_pirates"

    def __init__(self) -> None:
        # holidaypirates.com is the UK edition (GBP packages from UK airports); B21 reads the Italian site
        self.enabled = get_settings().enable_holiday_pirates
        self.disabled_reason = None if self.enabled else "UK edition, GBP packages (ENABLE_HOLIDAY_PIRATES=true)"

    @async_retry(
        max_attempts=3, min_wait=2.0, max_wait=15.0,
        retry_on=(httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError),
    )
    async def _fetch(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        resp = await client.get(url, headers=random_headers())
        resp.raise_for_status()
        return resp.text

    def _parse_flight_card(self, card: BeautifulSoup) -> Optional[RawFlightResult]:
        text = card.get_text(" ", strip=True)
        route = extract_route(text)
        price = extract_price(text)
        if not route or not price or not (5 < price[0] < 5000):
            return None

        link = card.find("a", href=True)
        booking_url = None
        if link:
            href = link["href"]
            booking_url = href if href.startswith("http") else urljoin(_BASE, href)

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

    def _extract_from_html(self, html: str) -> List[RawFlightResult]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.find_all("article") or soup.find_all(
            "div", class_=re.compile(r"deal|card|offer|item", re.I)
        )
        results = []
        for card in cards:
            r = self._parse_flight_card(card)
            if r:
                results.append(r)
        return results

    async def scrape_feed(self) -> List[RawFlightResult]:
        results: List[RawFlightResult] = []
        async with build_client(timeout=20.0) as client:
            discovered_flights, _ = await _discover_deal_urls(client)
            failed = 0

            for url in discovered_flights:
                try:
                    html = await self._fetch(client, url)
                    if html:
                        results.extend(self._extract_from_html(html))
                        if results:
                            break
                except Exception as exc:
                    failed += 1
                    log.debug("hp_flight_url_failed", url=url, error=str(exc))

            if not results:
                log.info("hp_flight_no_results", urls_tried=len(discovered_flights))
                if failed == len(discovered_flights):
                    self._record_error(f"all {failed} deal URLs failed")
        return results


class HolidayPiratesHotelScraper(BaseHotelScraper):
    """Scrapes HolidayPirates hotel deal pages (a feed: once per cycle, no search params)."""

    source_id = "holiday_pirates"
    capability = SearchCapability.FEED

    def __init__(self) -> None:
        self.enabled = get_settings().enable_holiday_pirates
        self.disabled_reason = None if self.enabled else "UK edition, GBP packages (ENABLE_HOLIDAY_PIRATES=true)"

    @async_retry(
        max_attempts=3, min_wait=2.0, max_wait=15.0,
        retry_on=(httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError),
    )
    async def _fetch(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        resp = await client.get(url, headers=random_headers())
        resp.raise_for_status()
        return resp.text

    def _parse_hotel_card(
        self, card: BeautifulSoup
    ) -> Optional[RawHotelResult]:
        text = card.get_text(separator=" ")
        # Posts quote the package price for the whole stay
        package_price = _first_price(text)
        if not package_price:
            return None

        discount = _first_discount(text)
        name_tag = card.find(["h2", "h3", "h4"])
        name = name_tag.get_text(strip=True) if name_tag else "Unknown Hotel"

        location = ""
        loc_pat = re.search(r"in\s+([A-Za-z\s,]+?)(?:\s*\||,|\n|$)", text)
        if loc_pat:
            location = loc_pat.group(1).strip()[:50]

        link = card.find("a", href=True)
        booking_url = None
        if link:
            href = link["href"]
            booking_url = href if href.startswith("http") else urljoin(_BASE, href)

        nights = _DEFAULT_HOTEL_NIGHTS

        return RawHotelResult(
            name=name,
            location=location or "Unknown",
            price_per_night=round(package_price / nights, 2),
            currency="EUR",
            nights=nights,
            booking_url=booking_url,
            source=self.source_id,
            extra={"discount_pct": discount},
        )

    async def scrape(self, tasks: Optional[List[SearchTask]] = None) -> List[RawHotelResult]:
        results: List[RawHotelResult] = []
        async with build_client(timeout=20.0) as client:
            _, discovered_hotels = await _discover_deal_urls(client)
            failed = 0

            for url in discovered_hotels:
                try:
                    html = await self._fetch(client, url)
                    if html:
                        soup = BeautifulSoup(html, "lxml")
                        cards = soup.find_all("article") or soup.find_all(
                            "div", class_=re.compile(r"deal|card|offer|hotel", re.I)
                        )
                        for card in cards:
                            r = self._parse_hotel_card(card)
                            if r:
                                results.append(r)
                        if results:
                            break
                except Exception as exc:
                    failed += 1
                    log.debug("hp_hotel_url_failed", url=url, error=str(exc))

            if not results:
                log.info("hp_hotel_no_results", urls_tried=len(discovered_hotels))
                if failed == len(discovered_hotels):
                    self._record_error(f"all {failed} deal URLs failed")
        return results
