"""
AI extraction of deals from deal-site posts (B21).

Deal sites write posts for people ("Capodanno a Parigi ... da 364€"); the
useful details sit in tables of departure airports, dates and prices. Claude
reads one post and returns the single best option departing from the user's
home airports, as JSON the SDK validates against FeedDealOption.

Nothing it returns is trusted as is: validate_option() drops an option whose
airports are unknown or not home airports, whose price is not written in the
post, or whose dates are in the past or out of order.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field

from utils import airports
from utils.airport_clusters import cluster_city_code
from utils.iata_extract import parse_amount
from utils.logging_config import get_logger

log = get_logger(__name__)

# Posts are a few thousand characters; this only guards against a runaway page
MAX_POST_CHARS = 20000


class FeedDealOption(BaseModel):
    """The best offer in one post, as the model reads it."""

    kind: Literal["flight", "package", "none"] = Field(
        description="flight = flights only; package = flight + hotel sold together; "
                    "none = no priced offer departing from a home airport, or not a flight/package offer",
    )
    origin_iata: Optional[str] = Field(description="IATA code of the departure airport of the chosen option")
    destination_iata: Optional[str] = Field(description="IATA code of the destination airport")
    price_eur: Optional[float] = Field(description="Price per person in euros, exactly as written for this option")
    round_trip: bool = Field(description="True when the price includes the return flight (A/R, packages)")
    departure_date: Optional[str] = Field(description="YYYY-MM-DD, only when the post gives the exact date")
    return_date: Optional[str] = Field(description="YYYY-MM-DD, only when the post gives the exact date")
    hotel_name: Optional[str] = Field(description="Hotel name for packages, when stated")
    travel_window: Optional[str] = Field(description="Travel period in English when there are no exact dates")


@dataclass
class FeedPost:
    """A deal-site post as sent to the model."""

    url: str
    title: str
    category: str
    text: str
    subtitle: str = ""
    headline_price: str = ""
    origins: Tuple[str, ...] = ()
    destinations: Tuple[str, ...] = ()
    published: Optional[date] = None
    expires: Optional[date] = None

    def render(self) -> str:
        text = self.text
        if len(text) > MAX_POST_CHARS:
            log.warning("feed_post_truncated", url=self.url, chars=len(text))
            text = text[:MAX_POST_CHARS]
        lines = [
            f"Title: {self.title}",
            f"Subtitle: {self.subtitle}",
            f"Category: {self.category}",
            f"Headline price: {self.headline_price}",
            f"Departure cities listed: {', '.join(self.origins) or 'not stated'}",
            f"Destinations: {', '.join(self.destinations) or 'not stated'}",
            f"Published: {self.published or 'unknown'}",
            f"Offer expires: {self.expires or 'not stated'}",
            "",
            text,
        ]
        return "\n".join(lines)


@dataclass
class ValidOption:
    """An extracted offer that passed every check (stored as the cache payload)."""

    kind: str
    origin: str
    destination: str
    price_eur: float
    round_trip: bool
    departure_date: Optional[str]
    return_date: Optional[str]
    hotel_name: Optional[str]
    travel_window: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _system_prompt(home_airports: Iterable[str], today: date) -> str:
    homes = ", ".join(f"{code} ({airports.get(code).name if airports.get(code) else code})" for code in home_airports)
    return (
        "You read Italian travel-deal posts from the site PiratinViaggio and extract the single best offer "
        f"for a traveller whose home airports are: {homes}. Today is {today.isoformat()}.\n"
        "- kind: 'flight' for flight-only offers, 'package' for flight + hotel packages, 'none' when the post "
        "has no priced offer departing from one of the home airports, or offers something else (cruise, tour "
        "without a clear price, hotel only).\n"
        "- Choose the cheapest option that departs from a home airport. Posts often list prices per departure "
        "airport and dates (for example '🔴 Da MILANO Malpensa' followed by '31.12 - 03.01 - 364€'): pick that "
        "row and give its airport's IATA code (MILANO Malpensa = MXP, MILANO Bergamo = BGY, MILANO Linate = LIN). "
        "When the post names only the city, give the city's IATA code if it has one (MIL for Milano, ROM for "
        "Roma), otherwise its main airport.\n"
        "- price_eur: the price per person for that option, exactly as written in the post.\n"
        "- departure_date / return_date: only when the post gives exact dates for that option. Dates without a "
        "year are the first such date after the publication date. Otherwise leave them null and describe the "
        "period in travel_window.\n"
        "- destination_iata: the destination airport; the main airport of the destination city if the post "
        "does not name one.\n"
        "Do not guess anything else: use null for what the post does not say."
    )


async def extract_option(client: Any, model: str, post: FeedPost, home_airports: List[str],
                         today: date) -> Optional[FeedDealOption]:
    """One post → the model's best option (None if it refused). API errors propagate to the caller."""
    response = await client.messages.parse(
        model=model,
        max_tokens=1024,
        system=_system_prompt(home_airports, today),
        messages=[{"role": "user", "content": post.render()}],
        output_format=FeedDealOption,
    )
    if response.stop_reason == "refusal":
        log.warning("feed_extraction_refused", url=post.url)
        return None
    return response.parsed_output


_AMOUNT_RES = (
    re.compile(r"(\d[\d.,]*)\s*(?:€|euro|EUR)", re.I),
    re.compile(r"€\s*(\d[\d.,]*)"),
)


def amounts_in(text: str) -> Set[float]:
    """Every euro amount written in the text."""
    found: Set[float] = set()
    for pattern in _AMOUNT_RES:
        for m in pattern.finditer(text):
            try:
                value = parse_amount(m.group(1).strip(".,"))
            except ValueError:
                continue
            if value:
                found.add(round(value, 2))
    return found


def _parse_date(value: Optional[str]) -> Tuple[bool, Optional[date]]:
    """(ok, date): ok is False for a malformed date."""
    if not value:
        return True, None
    try:
        return True, date.fromisoformat(value)
    except ValueError:
        return False, None


def _home_airport(code: str, home: List[str]) -> Optional[str]:
    """A home airport, or a city code (MIL) of one → the first home airport in that city."""
    if code in home:
        return code
    return next((airport for airport in home if cluster_city_code(airport) == code), None)


def validate_option(option: Optional[FeedDealOption], post: FeedPost, home: List[str],
                    today: date) -> Tuple[Optional[ValidOption], str]:
    """(valid option, '') or (None, why it was dropped). `home` is in preference order."""
    if option is None or option.kind == "none":
        return None, "no offer from a home airport"
    stated_origin = (option.origin_iata or "").strip().upper()
    origin = _home_airport(stated_origin, home)
    destination = (option.destination_iata or "").strip().upper()
    if origin is None:
        return None, f"origin {stated_origin or '?'} is not a home airport"
    if not airports.is_known(destination) or destination == origin:
        return None, f"unknown destination {destination or '?'}"
    price = option.price_eur
    if not price or price <= 0:
        return None, "no price"
    if round(price, 2) not in amounts_in(f"{post.title}\n{post.headline_price}\n{post.text}"):
        return None, f"price €{price:g} is not written in the post"

    ok_dep, departure = _parse_date(option.departure_date)
    ok_ret, return_date = _parse_date(option.return_date)
    if not (ok_dep and ok_ret):
        return None, "malformed date"
    if departure is None and return_date is not None:
        return None, "return date without a departure date"
    if departure is not None and departure < today:
        return None, f"departure {departure} is in the past"
    if departure and return_date and return_date <= departure:
        return None, "return is not after departure"

    return ValidOption(
        kind=option.kind,
        origin=origin,
        destination=destination,
        price_eur=round(price, 2),
        round_trip=option.round_trip or option.kind == "package",
        departure_date=departure.isoformat() if departure else None,
        return_date=return_date.isoformat() if return_date else None,
        hotel_name=(option.hotel_name or None) if option.kind == "package" else None,
        travel_window=None if departure else option.travel_window,
    ), ""
