"""
PiratinViaggio (the Italian HolidayPirates site) — deal posts read by Claude.

  1. The RSS feed (https://www.piratinviaggio.it/feed) lists about 30 recent
     posts. The URL path says what a post is: /voli/ (flights) and
     /pacchetti/ (flight + hotel packages) are read by PiratinViaggioScraper,
     /hotel/ by PiratinViaggioHotelScraper (hotel-only deals); cruises and
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
from typing import Any, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from ai_layer.extractor import (
    FeedPost,
    ValidHotelOption,
    ValidOption,
    extract_hotel_option,
    extract_option,
    validate_hotel_option,
    validate_option,
)
from ai_layer.formatter import _make_client
from config import get_settings
from preferences import get_preferences
from scheduler.planner import all_origins
from scrapers.base import BaseFeedScraper, BaseHotelScraper, SearchCapability, build_client, random_headers
from storage.database import get_feed_extractions, save_feed_extraction
from storage.models import RawFlightResult, RawHotelResult, SearchTask
from utils.logging_config import get_logger
from utils.markets import home_country

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
        return self.in_section(_DEAL_SECTIONS)

    def in_section(self, sections: Tuple[str, ...]) -> bool:
        return any(section in self.url for section in sections)


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


def describe_offer(option: Any) -> str:
    """One line per validated offer, for `main.py read-feed`."""
    if isinstance(option, ValidHotelOption):
        when = f"{option.check_in} → {option.check_out}" if option.check_in else option.travel_window
        return (f"hotel: {option.hotel_name}, {option.city}: €{option.price_per_night:g}/night "
                f"{option.price_basis}, {when or 'dates not given'}")
    arrow = "⇄" if option.round_trip else "→"
    when = option.departure_date and " → ".join(d for d in (option.departure_date, option.return_date) if d)
    stay = f" + {option.hotel_name}" if option.hotel_name else ""
    return (f"{option.kind}: {option.origin} {arrow} {option.destination}{stay}, €{option.price_eur:g}, "
            f"{when or option.travel_window or 'dates not given'}")


@dataclass
class Judgement:
    """What one post holds, as cached per URL."""

    outcome: str                    # "deal", "rejected: …", "skipped: …", "unreadable" or "error: …"
    post: Optional[FeedPost] = None
    option: Any = None              # the validated offer when outcome == "deal"
    cache: bool = True              # False for fetch and API errors: retried next cycle


class _PostReader:
    """
    Shared by the flight and hotel readers: feed → new posts in the reader's
    sections → Claude → validated option, cached per URL (each post is sent to
    the model once; API errors are not cached and retry next cycle).
    """

    source_id = "piratinviaggio"
    sections: Tuple[str, ...] = ()
    categories: Tuple[str, ...] = ()
    option_type: Any = None  # the dataclass stored as the cache payload

    def _init_reader(self) -> None:
        settings = get_settings()
        italian_home = home_country(get_preferences()) == "IT"  # an Italian site: departures from Italy
        self.enabled = settings.enable_piratinviaggio and bool(settings.anthropic_api_key) and italian_home
        if not self.enabled:
            if not settings.enable_piratinviaggio:
                reason = "switched off (ENABLE_PIRATINVIAGGIO=false)"
            elif not italian_home:
                reason = "home airports are not in Italy"
            else:
                reason = "needs ANTHROPIC_API_KEY (posts are read by Claude)"
            self.disabled_reason = reason
            log.info("scraper_disabled", source=self.source_id, reason=reason)

    async def _read(self, ai_client: Any, post: FeedPost, today: date) -> Tuple[Any, str]:
        """(valid option, '') or (None, reason) for one post."""
        raise NotImplementedError

    def _result(self, option: Any, url: str) -> Any:
        raise NotImplementedError

    @staticmethod
    def _starts(option: Any) -> Optional[date]:
        raise NotImplementedError

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

    async def _judge(self, client: httpx.AsyncClient, ai_client: Any, item: FeedItem, today: date) -> Judgement:
        """Read one post and decide what it holds; saves nothing."""
        html = await self._fetch(client, item.url)
        if html is None:
            return Judgement("error: page not fetched", cache=False)
        post = parse_post(html, item.url)
        if post is None:
            return Judgement("unreadable")
        if post.category not in self.categories:
            return Judgement(f"skipped: {post.category or 'no category'}", post=post)
        if post.expires and post.expires < today:
            return Judgement("skipped: expired", post=post)

        try:
            valid, reason = await self._read(ai_client, post, today)
        except Exception as exc:  # API or network error: not cached, retried next cycle
            self._record_error(f"{item.url}: {type(exc).__name__}: {exc}")
            return Judgement(f"error: {type(exc).__name__}: {exc}", post=post, cache=False)
        if valid is None:
            return Judgement(f"rejected: {reason}", post=post)
        return Judgement("deal", post=post, option=valid)

    async def _extract(self, client: httpx.AsyncClient, ai_client: Any, item: FeedItem, today: date) -> Any:
        """Read one new post and cache the outcome; None when it has no usable offer."""
        judgement = await self._judge(client, ai_client, item, today)
        if judgement.outcome.startswith("rejected: "):
            log.info("feed_post_rejected", url=item.url, reason=judgement.outcome.removeprefix("rejected: "))
        if judgement.cache:
            payload = json.dumps(judgement.option.to_dict()) if judgement.option else None
            await save_feed_extraction(item.url, self.source_id, judgement.outcome, payload)
        return judgement.option

    async def preview(self, limit: int) -> Optional[List[Tuple[FeedItem, Judgement]]]:
        """
        Read the `limit` newest posts in this reader's sections with the real model,
        ignoring and never writing the cache (`main.py read-feed`). None when the
        feed can't be fetched.
        """
        settings = get_settings()
        today = _now().date()
        async with build_client(timeout=25.0) as client:
            xml = await self._fetch(client, FEED_URL)
            if xml is None:
                return None
            items = [i for i in parse_feed(xml) if i.in_section(self.sections)][:limit]
            ai_client = _make_client(
                settings.anthropic_api_key, max(settings.ai_timeout_seconds, _MIN_EXTRACTION_TIMEOUT_S)
            )
            return [(item, await self._judge(client, ai_client, item, today)) for item in items]

    async def _collect(self) -> List[Any]:
        settings = get_settings()
        now = _now()
        today = now.date()
        results: List[Any] = []

        async with build_client(timeout=25.0) as client:
            xml = await self._fetch(client, FEED_URL)
            if xml is None:
                return []
            items = [
                i for i in parse_feed(xml)
                if i.in_section(self.sections)
                and (i.published is None or now - i.published <= timedelta(days=FEED_MAX_AGE_DAYS))
            ]
            cached = await get_feed_extractions([i.url for i in items])
            budget = settings.ai_extraction_max_posts_per_cycle
            ai_client = None

            for item in items:
                if item.url in cached:
                    status, payload = cached[item.url]
                    try:
                        option = self.option_type(**json.loads(payload)) if status == "deal" and payload else None
                    except (TypeError, ValueError):  # cached by an older version: skip it
                        option = None
                elif budget > 0:
                    budget -= 1
                    ai_client = ai_client or _make_client(
                        settings.anthropic_api_key, max(settings.ai_timeout_seconds, _MIN_EXTRACTION_TIMEOUT_S)
                    )
                    option = await self._extract(client, ai_client, item, today)
                else:
                    log.info("feed_extraction_budget_reached", source=self.source_id, url=item.url)
                    continue

                if option is None:
                    continue
                starts = self._starts(option)
                if starts and starts < today:
                    continue  # a cached offer whose date has passed
                results.append(self._result(option, item.url))

        log.info("piratinviaggio_deals", source=self.source_id, posts=len(items), deals=len(results))
        return results


class PiratinViaggioScraper(_PostReader, BaseFeedScraper):
    """Flight and flight + hotel package posts (/voli/, /pacchetti/)."""

    source_id = "piratinviaggio"
    sections = _DEAL_SECTIONS
    categories = _DEAL_CATEGORIES
    option_type = ValidOption

    def __init__(self) -> None:
        self._init_reader()

    async def _read(self, ai_client: Any, post: FeedPost, today: date) -> Tuple[Any, str]:
        home = all_origins(get_preferences())
        option = await extract_option(ai_client, get_settings().ai_model, post, home, today)
        return validate_option(option, post, home, today)

    @staticmethod
    def _starts(option: ValidOption) -> Optional[date]:
        return date.fromisoformat(option.departure_date) if option.departure_date else None

    def _result(self, option: ValidOption, url: str) -> RawFlightResult:
        return self.result_from(option, url)

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

    async def scrape_feed(self) -> List[RawFlightResult]:
        return await self._collect()


class PiratinViaggioHotelScraper(_PostReader, BaseHotelScraper):
    """Hotel posts (/hotel/) → hotel-only deals (B19)."""

    source_id = "piratinviaggio_hotels"
    capability = SearchCapability.FEED
    sections = ("/hotel/",)
    categories = ("HOTEL",)
    option_type = ValidHotelOption

    def __init__(self) -> None:
        self._init_reader()

    async def _read(self, ai_client: Any, post: FeedPost, today: date) -> Tuple[Any, str]:
        option = await extract_hotel_option(ai_client, get_settings().ai_model, post, today)
        return validate_hotel_option(option, post, today)

    @staticmethod
    def _starts(option: ValidHotelOption) -> Optional[date]:
        return date.fromisoformat(option.check_in) if option.check_in else None

    def _result(self, option: ValidHotelOption, url: str) -> RawHotelResult:
        check_in = date.fromisoformat(option.check_in) if option.check_in else None
        check_out = date.fromisoformat(option.check_out) if option.check_out else None
        return RawHotelResult(
            name=option.hotel_name,
            location=option.city,
            price_per_night=option.price_per_night,
            currency="EUR",
            nights=option.nights,
            rating=option.rating,
            stars=option.stars,
            review_count=option.review_count,
            booking_url=url,
            source=self.source_id,
            check_in=check_in,
            check_out=check_out,
            is_feed_deal=True,
            price_basis=option.price_basis,
            travel_window=option.travel_window,
        )

    async def scrape(self, tasks: Optional[List[SearchTask]] = None) -> List[RawHotelResult]:
        return await self._collect()
