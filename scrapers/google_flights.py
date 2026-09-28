from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta
from typing import Any, List, Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseFlightScraper, build_client, random_headers
from storage.models import RawFlightResult, ScraperParams
from utils.logging_config import get_logger
from utils.retry import async_retry

log = get_logger(__name__)


class GoogleFlightsScraper(BaseFlightScraper):
    """
    Google Flights HTML scraper.

    Uses Google Flights' URL scheme to load search results, then extracts
    structured data from JSON-LD script tags and inline JS data objects.
    Falls back to best-effort regex parsing.

    No API key required — uses public web interface.
    May return 0 results if Google detects bot traffic.
    """

    source_id = "google_flights"

    @async_retry(
        max_attempts=3, min_wait=3.0, max_wait=20.0,
        retry_on=(httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError),
    )
    async def _fetch_search_page(
        self,
        client: httpx.AsyncClient,
        origin: str,
        dest: str,
        dep_date: date,
        return_date: Optional[date],
    ) -> Optional[str]:
        dep_encoded = dep_date.strftime("%Y%m%d")
        if return_date:
            ret_encoded = return_date.strftime("%Y%m%d")
            url = (
                f"https://www.google.com/travel/flights?hl=en&curr=EUR"
                f"&q={quote(f'flights from {origin} to {dest}')}"
                f"&departure={dep_encoded}&return={ret_encoded}"
            )
        else:
            url = (
                f"https://www.google.com/travel/flights?hl=en&curr=EUR"
                f"&q={quote(f'flights from {origin} to {dest}')}"
                f"&departure={dep_encoded}"
            )

        headers = random_headers(
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.google.com/",
            }
        )
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            return resp.text
        log.warning("gf_bad_status", status=resp.status_code, origin=origin, dest=dest)
        self._record_error(f"{origin}-{dest}: HTTP {resp.status_code}")
        return None

    def _extract_prices(self, html: str, origin: str, dest: str, dep_date: date) -> List[RawFlightResult]:
        results: List[RawFlightResult] = []
        soup = BeautifulSoup(html, "lxml")

        # Try JSON-LD structured data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    for item in data:
                        r = self._parse_ld_item(item, origin, dest, dep_date)
                        if r:
                            results.append(r)
                else:
                    r = self._parse_ld_item(data, origin, dest, dep_date)
                    if r:
                        results.append(r)
            except Exception:
                pass

        # Try to extract price data from inline JS variables
        price_pattern = re.compile(r'"price":\s*(\d+(?:\.\d+)?)')
        airline_pattern = re.compile(r'"name":\s*"([A-Za-z\s]+(?:Airlines?|Airways?|Air\s+\w+)?)"')
        prices = [float(m.group(1)) for m in price_pattern.finditer(html)]
        airlines = [m.group(1) for m in airline_pattern.finditer(html)]

        for i, price in enumerate(prices[:5]):
            if price < 20 or price > 5000:
                continue
            airline = airlines[i] if i < len(airlines) else None
            results.append(
                RawFlightResult(
                    origin=origin,
                    destination=dest,
                    price=price,
                    currency="EUR",
                    departure_date=dep_date,
                    airline=airline,
                    source=self.source_id,
                )
            )

        return results

    def _parse_ld_item(
        self, item: Any, origin: str, dest: str, dep_date: date
    ) -> Optional[RawFlightResult]:
        if not isinstance(item, dict):
            return None
        price = item.get("price") or item.get("offers", {}).get("price")
        if not price:
            return None
        try:
            return RawFlightResult(
                origin=origin,
                destination=dest,
                price=float(price),
                currency=item.get("priceCurrency", "EUR"),
                departure_date=dep_date,
                airline=item.get("provider", {}).get("name"),
                source=self.source_id,
            )
        except Exception:
            return None

    async def scrape(self, params: ScraperParams) -> List[RawFlightResult]:
        results: List[RawFlightResult] = []
        async with build_client(timeout=25.0) as client:
            tasks = []
            for origin in params.origins[:3]:
                for dest in params.destinations[:8]:
                    dep = params.departure_date_from
                    ret = dep + timedelta(days=params.nights_min + 1) if params.nights_min else None
                    tasks.append((origin, dest, dep, ret))

            for origin, dest, dep, ret in tasks:
                try:
                    html = await self._fetch_search_page(client, origin, dest, dep, ret)
                    if html:
                        found = self._extract_prices(html, origin, dest, dep)
                        results.extend(found)
                    await asyncio.sleep(1.5)
                except Exception as exc:
                    log.warning("gf_scrape_pair_failed", origin=origin, dest=dest, error=str(exc))
                    self._record_error(f"{origin}-{dest}: {exc}")
        return results
