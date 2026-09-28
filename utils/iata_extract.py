"""
Extract routes, prices and travel windows from deal-post text.

Deal feeds (Secret Flying, HolidayPirates, Going) are free text. Any
3-letter word is not an airport — THE, FOR and BUS are real IATA codes — so
codes are only accepted from route-shaped patterns ("MXP – KRK",
"Milan (MXP) to Tokyo (NRT)", "Milan, Italy to New York, USA for €189"),
and every airport must resolve in the registry. Prices must sit next to a
currency symbol or code.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from utils import airports

# "MXP – KRK", "MXP-KRK", "MXP → KRK", "MXP to KRK"
_CODE_PAIR_RE = re.compile(r"\b([A-Z]{3})\s*(?:-|–|—|→|->|>|to)\s*([A-Z]{3})\b")
# "Milan (MXP)"
_PAREN_CODE_RE = re.compile(r"\(([A-Z]{3})\)")

_NAME = r"[A-Z][A-Za-zÀ-ÿ.'’ -]{1,40}?"
_COUNTRY = r"(?:,\s*[A-Z][A-Za-zÀ-ÿ.'’ -]{1,30}?)?"
# "from Milan to Krakow for €25", "Milan, Italy to New York, USA for only €189"
_CITY_PAIR_RE = re.compile(
    rf"(?:\bfrom\s+)?(?P<o>{_NAME}){_COUNTRY}\s+(?:to|→|–|—)\s+(?P<d>{_NAME}){_COUNTRY}"
    r"(?=\s+(?:for|from|only|at|in|by)\b|\s*[€$£(:!.,]|\s*$)"
)

_AMOUNT = r"\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}
_PRICE_RES = [
    re.compile(rf"(?P<cur>[€$£])\s?(?P<amt>{_AMOUNT})"),
    re.compile(rf"\b(?P<cur>EUR|USD|GBP)\s?(?P<amt>{_AMOUNT})"),
    re.compile(rf"(?P<amt>{_AMOUNT})\s?(?P<cur>€|EUR\b|USD\b|GBP\b)"),
]

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_WINDOW_RE = re.compile(rf"\b{_MONTH}(?:\s+\d{{4}})?\s*(?:-|–|—|to|until)\s*{_MONTH}(?:\s+\d{{4}})?", re.I)


def _known(code: str) -> bool:
    return airports.is_known(code)


def extract_route(text: str) -> Optional[Tuple[str, str]]:
    """(origin, destination) IATA codes, or None if no trustworthy route is found."""
    for m in _CODE_PAIR_RE.finditer(text):
        origin, dest = m.group(1), m.group(2)
        if origin != dest and _known(origin) and _known(dest):
            return origin, dest

    codes: List[str] = []
    for code in _PAREN_CODE_RE.findall(text):
        if _known(code) and code not in codes:
            codes.append(code)
    if len(codes) >= 2:
        return codes[0], codes[1]

    for m in _CITY_PAIR_RE.finditer(text):
        origin = _resolve_city(m.group("o"), from_end=True)
        dest = _resolve_city(m.group("d"), from_end=False)
        if origin and dest and origin != dest:
            return origin, dest
    return None


def _resolve_city(name: str, from_end: bool) -> Optional[str]:
    """
    Match a city name that may carry extra words: the origin is the tail of
    "Cheap flights from Milan", the destination the head of "Krakow return".
    """
    words = name.split()
    for i in range(len(words)):
        candidate = " ".join(words[i:] if from_end else words[: len(words) - i])
        code = airports.iata_for_city(candidate)
        if code:
            return code
    return None


def parse_amount(raw: str) -> Optional[float]:
    """'1,234' → 1234, '1.234' → 1234, '12,50' → 12.5, '1,234.56' → 1234.56."""
    s = raw.replace(" ", "").replace(" ", "")
    if not s:
        return None
    last_sep = max(s.rfind(","), s.rfind("."))
    if last_sep == -1:
        return float(s)
    decimals = len(s) - last_sep - 1
    if decimals in (1, 2):
        integer = re.sub(r"[.,]", "", s[:last_sep])
        return float(f"{integer}.{s[last_sep + 1:]}")
    # 3 digits after the last separator → thousands separator
    return float(re.sub(r"[.,]", "", s))


def extract_price(text: str) -> Optional[Tuple[float, str]]:
    """First currency-marked price in the text as (amount, ISO currency)."""
    best: Optional[Tuple[int, float, str]] = None
    for pattern in _PRICE_RES:
        for m in pattern.finditer(text):
            amount = parse_amount(m.group("amt"))
            if amount is None or amount <= 0:
                continue
            currency = _SYMBOLS.get(m.group("cur"), m.group("cur").upper())
            if best is None or m.start() < best[0]:
                best = (m.start(), amount, currency)
            break
    return (best[1], best[2]) if best else None


def extract_travel_window(text: str) -> Optional[str]:
    """Free-text travel window such as 'November – March 2027', if the post states one."""
    m = _WINDOW_RE.search(text)
    return m.group(0).strip() if m else None
