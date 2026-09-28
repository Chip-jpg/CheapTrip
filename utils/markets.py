"""Where the user lives, as airline and search sites want it: country and market."""
from __future__ import annotations

from typing import Optional

from preferences import UserPreferences
from utils import airports

# Ryanair markets are language-country; other countries use the English (UK) site
_RYANAIR_MARKETS = {
    "IT": "it-it", "GB": "en-gb", "IE": "en-ie", "ES": "es-es", "DE": "de-de", "FR": "fr-fr",
    "PT": "pt-pt", "NL": "nl-nl", "PL": "pl-pl", "BE": "fr-be", "AT": "de-at", "DK": "da-dk",
    "SE": "sv-se", "NO": "no-no", "CZ": "cs-cz", "HU": "hu-hu", "GR": "el-gr", "MT": "en-mt",
}
DEFAULT_MARKET = "en-gb"


def home_country(prefs: UserPreferences) -> Optional[str]:
    """ISO country of the first known home airport."""
    for code in prefs.home_airports:
        airport = airports.get(code)
        if airport:
            return airport.country
    return None


def market_for(prefs: UserPreferences) -> str:
    """The preferences' market, or one derived from the home country ("it-it", "en-gb", ...)."""
    if prefs.market:
        return prefs.market.lower()
    return _RYANAIR_MARKETS.get(home_country(prefs) or "", DEFAULT_MARKET)


def first_home_airport(prefs: UserPreferences) -> str:
    return (prefs.home_airports or ["MXP"])[0].upper()
