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
        "row and give its airport's IATA code (e.g. 'MILANO Malpensa' = MXP, 'ROMA Fiumicino' = FCO). "
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


# ── Hotel posts (B19) ─────────────────────────────────────────────────────────

class FeedHotelOption(BaseModel):
    """The hotel offer in one post, as the model reads it."""

    kind: Literal["hotel", "none"] = Field(
        description="hotel = a priced hotel stay; none = no priced hotel offer (list articles, several hotels)",
    )
    hotel_name: Optional[str] = Field(description="The hotel's name as written")
    city: Optional[str] = Field(description="City or town where the hotel is, in English")
    country_code: Optional[str] = Field(description="ISO 3166 two-letter country code")
    price_eur: Optional[float] = Field(description="The cheapest price in euros, exactly as written in the post")
    price_basis: Literal["per_person_per_night", "per_room_per_night", "per_person_stay", "per_room_stay"] = Field(
        description="What that price covers",
    )
    nights: Optional[int] = Field(description="Nights the price is for, when stated")
    check_in: Optional[str] = Field(description="YYYY-MM-DD, only when the post gives the exact date")
    check_out: Optional[str] = Field(description="YYYY-MM-DD, only when the post gives the exact date")
    travel_window: Optional[str] = Field(description="Travel period in English when there are no exact dates")
    rating_out_of_10: Optional[float] = Field(description="Guest rating converted to a 0-10 scale, when stated")
    review_count: Optional[int] = Field(description="Number of reviews, when stated")
    stars: Optional[int] = Field(description="Hotel class (1-5 stars), when stated")


@dataclass
class ValidHotelOption:
    """A hotel offer that passed every check (stored as the cache payload)."""

    kind: str
    hotel_name: str
    city: str
    country_code: Optional[str]
    price_per_night: float
    price_basis: str               # "per person" / "per room"
    nights: int
    check_in: Optional[str]
    check_out: Optional[str]
    travel_window: Optional[str]
    rating: Optional[float]
    review_count: Optional[int]
    stars: Optional[int]

    def to_dict(self) -> dict:
        return asdict(self)


def _hotel_system_prompt(today: date) -> str:
    return (
        "You read Italian hotel-deal posts from the site PiratinViaggio and extract the offer. "
        f"Today is {today.isoformat()}.\n"
        "- kind: 'hotel' for a priced stay in one hotel; 'none' for lists of many hotels or posts without a price.\n"
        "- price_eur: the cheapest price exactly as written (e.g. '48,50€/persona' → 48.5), and price_basis says "
        "what it covers ('a persona' = per person; '/tot.' or 'a camera' = per room; a price for a whole stay of "
        "several nights = *_stay with nights).\n"
        "- check_in / check_out: only exact dates; dates without a year are the first such date after today. "
        "Otherwise describe the period in travel_window (in English, e.g. 'October to December').\n"
        "- rating_out_of_10: convert other scales ('4,98/5' → 9.96).\n"
        "Do not guess: use null for what the post does not say."
    )


async def extract_hotel_option(client: Any, model: str, post: FeedPost, today: date) -> Optional[FeedHotelOption]:
    response = await client.messages.parse(
        model=model,
        max_tokens=1024,
        system=_hotel_system_prompt(today),
        messages=[{"role": "user", "content": post.render()}],
        output_format=FeedHotelOption,
    )
    if response.stop_reason == "refusal":
        log.warning("feed_extraction_refused", url=post.url)
        return None
    return response.parsed_output


def validate_hotel_option(option: Optional[FeedHotelOption], post: FeedPost,
                          today: date) -> Tuple[Optional[ValidHotelOption], str]:
    """(valid option, '') or (None, why it was dropped)."""
    if option is None or option.kind == "none":
        return None, "no priced hotel offer"
    name, city = (option.hotel_name or "").strip(), (option.city or "").strip()
    if not name or not city:
        return None, "no hotel name or city"
    price = option.price_eur
    if not price or price <= 0:
        return None, "no price"
    if round(price, 2) not in amounts_in(f"{post.title}\n{post.headline_price}\n{post.text}"):
        return None, f"price €{price:g} is not written in the post"

    ok_in, check_in = _parse_date(option.check_in)
    ok_out, check_out = _parse_date(option.check_out)
    if not (ok_in and ok_out):
        return None, "malformed date"
    if (check_in is None) != (check_out is None):
        return None, "only one of check-in / check-out"
    if check_in and check_in < today:
        return None, f"check-in {check_in} is in the past"
    if check_in and check_out <= check_in:
        return None, "check-out is not after check-in"

    nights = (check_out - check_in).days if check_in else (option.nights or 1)
    if nights < 1:
        return None, "no nights"
    per_stay = option.price_basis.endswith("_stay")
    if per_stay and not (check_in or option.nights):
        return None, "a price for the stay without its length"
    rating = option.rating_out_of_10
    if rating is not None and not 0 < rating <= 10:
        rating = None

    return ValidHotelOption(
        kind="hotel",
        hotel_name=name,
        city=city,
        country_code=(option.country_code or "").upper() or None,
        price_per_night=round(price / nights, 2) if per_stay else round(price, 2),
        price_basis="per person" if option.price_basis.startswith("per_person") else "per room",
        nights=nights,
        check_in=check_in.isoformat() if check_in else None,
        check_out=check_out.isoformat() if check_out else None,
        travel_window=None if check_in else option.travel_window,
        rating=rating,
        review_count=option.review_count if option.review_count and option.review_count > 0 else None,
        stars=option.stars if option.stars and 1 <= option.stars <= 5 else None,
    ), ""
