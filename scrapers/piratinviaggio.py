"""
PiratinViaggio (the Italian HolidayPirates site) — deal posts read by Claude.

  1. The RSS feed (https://www.piratinviaggio.it/feed) lists about 30 recent
     posts. The URL path says what a post is: /voli/ (flights) and
     /pacchetti/ (flight + hotel packages) are read; /hotel/, /crociere/ and
     magazine articles are skipped without fetching them.
  2. A post page embeds the post as JSON (window.__staticRouterHydrationData):
     title, headline price, departure cities, expiry date and the body, which
     usually lists prices per departure airport and dates, e.g.
       "🔴 Da MILANO Malpensa · 31.12 - 03.01 - 364€".
  3. Claude picks the best option from the user's home airports
     (ai_layer/extractor.py); the answer is validated against the post and
     cached per URL, so each post is sent to the model once.

Runs only when ANTHROPIC_API_KEY is set (ENABLE_PIRATINVIAGGIO turns it off).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, List, Optional

import httpx
from bs4 import BeautifulSoup

from ai_layer.extractor import FeedPost, ValidOption, extract_option, validate_option
from ai_layer.formatter import _make_client
from config import get_settings
from preferences import get_preferences
from scheduler.planner import all_origins
from scrapers.base import BaseFeedScraper, build_client, random_headers
from storage.database import get_feed_extractions, save_feed_extraction
from storage.models import RawFlightResult
from utils.logging_config import get_logger

log = get_logger(__name__)

FEED_URL = "https://www.piratinviaggio.it/feed"
FEED_MAX_AGE_DAYS = 7
_DEAL_SECTIONS = ("/voli/", "/pacchetti/")
_DEAL_CATEGORIES = ("FLIGHT", "PACKAGE")
_HYDRATION_MARK = "window.__staticRouterHydrationData"
# Extraction can take longer than the alert polish call
_MIN_EXTRACTION_TIMEOUT_S = 30.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FeedItem:
    title: str
    url: str
    published: Optional[datetime]

    @property
    def is_deal_section(self) -> bool:
        return any(section in self.url for section in _DEAL_SECTIONS)


def parse_feed(xml: str) -> List[FeedItem]:
    items: List[FeedItem] = []
    for item in BeautifulSoup(xml, "xml").find_all("item"):
        link, title, pub = item.find("link"), item.find("title"), item.find("pubDate")
        if not link or not link.text.strip():
            continue
        try:
            published = parsedate_to_datetime(pub.text.strip()) if pub else None
        except (TypeError, ValueError):
            published = None
        items.append(FeedItem(title=title.text.strip() if title else "", url=link.text.strip(), published=published))
    return items


def _rich_text(node: Any, out: List[str]) -> None:
    """Contentful rich text → plain text, one line per paragraph / heading / list item."""
    if isinstance(node, list):
        for child in node:
            _rich_text(child, out)
        return
    if not isinstance(node, dict):
        return
    node_type = node.get("nodeType", "")
    if node_type == "text":
        out.append(node.get("value", ""))
        return
    for key, value in node.items():
        if key != "data":
            _rich_text(value, out)
    if node_type.startswith(("heading", "paragraph", "list-item", "table-row")):
        out.append("\n")


def _iso_date(value: Optional[str]) -> Optional[date]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date() if value else None
    except ValueError:
        return None


def parse_post(html: str, url: str) -> Optional[FeedPost]:
    """The post embedded in a PiratinViaggio page; None when the page has no post data."""
    start = html.find(_HYDRATION_MARK)
    if start == -1:
        return None
    parse_at = html.find("JSON.parse(", start)
    if parse_at == -1:
        return None
    try:
        literal, _ = json.JSONDecoder().raw_decode(html, parse_at + len("JSON.parse("))
        data = json.loads(literal)
    except (ValueError, TypeError):
        return None
    post = next((v for k, v in (data.get("loaderData") or {}).items() if k.startswith("post--") and v), None)
    if not isinstance(post, dict):
        return None

    lines: List[str] = []
    _rich_text(post.get("textContent"), lines)
    text = re.sub(r"\n\s*\n+", "\n", "".join(lines)).strip()
    pricing = post.get("pricing") or {}
    headline = " ".join(str(pricing.get(k) or "") for k in ("prefix", "amount", "suffix")).strip()
    return FeedPost(
        url=url,
        title=post.get("title") or "",
        subtitle=post.get("subtitle") or "",
        category=((post.get("category") or {}).get("key") or "").upper(),
        headline_price=headline,
        origins=tuple(post.get("originNames") or ()),
        destinations=tuple(post.get("destinationNames") or ()),
        published=_iso_date(post.get("publishedAt")),
        expires=_iso_date(post.get("expirationDate")) if not post.get("expired") else _now().date() - timedelta(days=1),
        text=text,
    )


class PiratinViaggioScraper(BaseFeedScraper):
    source_id = "piratinviaggio"

    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.enable_piratinviaggio and bool(settings.anthropic_api_key)
        if not self.enabled:
            log.info("scraper_disabled", source=self.source_id,
                     reason="needs ANTHROPIC_API_KEY (posts are read by Claude)")

    def result_from(self, option: ValidOption, url: str) -> RawFlightResult:
        return RawFlightResult(
            origin=option.origin,
            destination=option.destination,
            price=option.price_eur,
            currency="EUR",
            departure_date=date.fromisoformat(option.departure_date) if option.departure_date else None,
            return_date=date.fromisoformat(option.return_date) if option.return_date else None,
            booking_url=url,
            source=self.source_id,
            travel_window=option.travel_window,
            is_round_trip=option.round_trip,
            is_feed_deal=True,
            is_package=option.kind == "package",
            package_hotel=option.hotel_name,
        )

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        try:
            resp = await client.get(url, headers=random_headers({"Accept-Language": "it-IT,it;q=0.9"}))
        except httpx.HTTPError as exc:
            self._record_error(f"{url}: {exc}")
            return None
        if resp.status_code != 200:
            self._record_error(f"{url}: HTTP {resp.status_code}")
            return None
        return resp.text

    async def _extract(self, client: httpx.AsyncClient, ai_client: Any, item: FeedItem,
                       home: List[str], today: date) -> Optional[ValidOption]:
        """Read one new post and cache the outcome; None when it has no usable offer."""
        html = await self._fetch(client, item.url)
        if html is None:
            return None  # not cached: retried next cycle
        post = parse_post(html, item.url)
        if post is None:
            await save_feed_extraction(item.url, self.source_id, "unreadable")
            return None
        if post.category not in _DEAL_CATEGORIES:
            await save_feed_extraction(item.url, self.source_id, f"skipped: {post.category or 'no category'}")
            return None
        if post.expires and post.expires < today:
            await save_feed_extraction(item.url, self.source_id, "skipped: expired")
            return None

        settings = get_settings()
        try:
            option = await extract_option(ai_client, settings.ai_model, post, home, today)
        except Exception as exc:  # API or network error: not cached, retried next cycle
            self._record_error(f"{item.url}: {type(exc).__name__}: {exc}")
            return None

        valid, reason = validate_option(option, post, home, today)
        if valid is None:
            log.info("feed_post_rejected", url=item.url, reason=reason)
            await save_feed_extraction(item.url, self.source_id, f"rejected: {reason}")
            return None
        await save_feed_extraction(item.url, self.source_id, "deal", json.dumps(valid.to_dict()))
        return valid

    async def scrape_feed(self) -> List[RawFlightResult]:
        settings = get_settings()
        home = all_origins(get_preferences())
        now = _now()
        today = now.date()
        results: List[RawFlightResult] = []

        async with build_client(timeout=25.0) as client:
            xml = await self._fetch(client, FEED_URL)
            if xml is None:
                return []
            items = [
                i for i in parse_feed(xml)
                if i.is_deal_section and (i.published is None or now - i.published <= timedelta(days=FEED_MAX_AGE_DAYS))
            ]
            cached = await get_feed_extractions([i.url for i in items])
            budget = settings.ai_extraction_max_posts_per_cycle
            ai_client = None

            for item in items:
                if item.url in cached:
                    status, payload = cached[item.url]
                    try:
                        option = ValidOption(**json.loads(payload)) if status == "deal" and payload else None
                    except (TypeError, ValueError):  # cached by an older version: skip it
                        option = None
                elif budget > 0:
                    budget -= 1
                    ai_client = ai_client or _make_client(
                        settings.anthropic_api_key, max(settings.ai_timeout_seconds, _MIN_EXTRACTION_TIMEOUT_S)
                    )
                    option = await self._extract(client, ai_client, item, home, today)
                else:
                    log.info("feed_extraction_budget_reached", source=self.source_id, url=item.url)
                    continue

                if option is None:
                    continue
                if option.departure_date and date.fromisoformat(option.departure_date) < today:
                    continue  # a cached offer whose departure has passed
                results.append(self.result_from(option, item.url))

        log.info("piratinviaggio_deals", posts=len(items), deals=len(results))
        return results
