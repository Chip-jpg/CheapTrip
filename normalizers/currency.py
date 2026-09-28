from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

import httpx

from config import get_settings
from utils.logging_config import get_logger

log = get_logger(__name__)

# Fallback static rates (EUR base, updated periodically)
_FALLBACK_RATES: Dict[str, float] = {
    "EUR": 1.0,
    "USD": 1.08,
    "GBP": 0.86,
    "CHF": 0.97,
    "SEK": 11.55,
    "NOK": 11.60,
    "DKK": 7.46,
    "PLN": 4.30,
    "CZK": 25.30,
    "HUF": 395.0,
    "RON": 4.97,
    "BGN": 1.956,
    "HRK": 7.53,
    "TRY": 35.0,
    "AED": 3.97,
    "THB": 38.5,
    "SGD": 1.46,
    "JPY": 162.0,
    "KRW": 1480.0,
    "CNY": 7.82,
    "INR": 90.0,
    "BRL": 5.50,
    "ARS": 1050.0,
    "AUD": 1.65,
    "NZD": 1.78,
    "CAD": 1.48,
    "MXN": 18.5,
}

# Airport code → city name (for hotel searches)
AIRPORT_TO_CITY: Dict[str, str] = {
    "MXP": "Milan",
    "LIN": "Milan",
    "BGY": "Milan",
    "VCE": "Venice",
    "VRN": "Verona",
    "BLQ": "Bologna",
    "FCO": "Rome",
    "CIA": "Rome",
    "LHR": "London",
    "LGW": "London",
    "AMS": "Amsterdam",
    "CDG": "Paris",
    "FRA": "Frankfurt",
    "MAD": "Madrid",
    "BCN": "Barcelona",
    "DUB": "Dublin",
    "KRK": "Krakow",
    "WAW": "Warsaw",
    "PRG": "Prague",
    "BUD": "Budapest",
    "LIS": "Lisbon",
    "ATH": "Athens",
    "CPH": "Copenhagen",
    "ARN": "Stockholm",
    "HEL": "Helsinki",
    "OSL": "Oslo",
    "VIE": "Vienna",
    "ZRH": "Zurich",
    "BRU": "Brussels",
    "EDI": "Edinburgh",
    "GVA": "Geneva",
    "NCE": "Nice",
    "MRS": "Marseille",
    "OPO": "Porto",
    "PMI": "Palma",
    "IBZ": "Ibiza",
    "TFS": "Tenerife",
    "JFK": "New York",
    "EWR": "New York",
    "LAX": "Los Angeles",
    "MIA": "Miami",
    "ORD": "Chicago",
    "BOS": "Boston",
    "YYZ": "Toronto",
    "YVR": "Vancouver",
    "NRT": "Tokyo",
    "HND": "Tokyo",
    "ICN": "Seoul",
    "HKG": "Hong Kong",
    "BKK": "Bangkok",
    "SIN": "Singapore",
    "KUL": "Kuala Lumpur",
    "DXB": "Dubai",
    "AUH": "Abu Dhabi",
    "DOH": "Doha",
    "TLV": "Tel Aviv",
    "CAI": "Cairo",
    "GRU": "São Paulo",
    "EZE": "Buenos Aires",
    "JNB": "Johannesburg",
    "CPT": "Cape Town",
    "SYD": "Sydney",
    "MEL": "Melbourne",
    "AKL": "Auckland",
}


class CurrencyConverter:
    """
    Async currency converter.
    Fetches live rates from exchangerate-api.com; falls back to static rates.
    """

    _live_rates: Dict[str, float] = {}
    _fetched_at: Optional[datetime] = None
    _CACHE_TTL_HOURS = 6

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.exchange_rate_api_key

    def _is_stale(self) -> bool:
        if not self._fetched_at:
            return True
        age = (datetime.utcnow() - self._fetched_at).total_seconds() / 3600
        return age >= self._CACHE_TTL_HOURS

    async def fetch_live_rates(self) -> None:
        if not self._api_key:
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"https://v6.exchangerate-api.com/v6/{self._api_key}/latest/EUR"
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                if data.get("result") == "success":
                    CurrencyConverter._live_rates = data["conversion_rates"]
                    CurrencyConverter._fetched_at = datetime.utcnow()
                    log.info("currency_rates_refreshed", count=len(self._live_rates))
        except Exception as exc:
            log.warning("currency_fetch_failed", error=str(exc), fallback="static_rates")

    def to_eur(self, amount: float, currency: str) -> float:
        currency = currency.upper().strip()
        if currency == "EUR":
            return round(amount, 2)

        rates = self._live_rates or _FALLBACK_RATES
        rate = rates.get(currency, 1.0) or 1.0
        eur_per_currency = 1.0 / rate
        return round(amount * eur_per_currency, 2)

    async def ensure_fresh(self) -> None:
        if self._is_stale():
            await self.fetch_live_rates()


# Module-level singleton
_converter: Optional[CurrencyConverter] = None


def get_converter() -> CurrencyConverter:
    global _converter
    if _converter is None:
        _converter = CurrencyConverter()
    return _converter
