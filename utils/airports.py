"""
Airport registry — the single source of truth for airport metadata.

Data comes from utils/data/airports.csv (large and medium airports with
scheduled service, from the public-domain OurAirports dataset; regenerate with
scripts/build_airports_csv.py). On top of that this module defines:

- city clusters (airports serving the same city, with their IATA city code)
- curated city names (the dataset's "municipality" is often a suburb)
- beach destinations
- region classification and distance-based flight-time estimates

Airports a data source reports that are not in the dataset can be added at
runtime with learn().
"""
from __future__ import annotations

import csv
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_DATA_PATH = Path(__file__).parent / "data" / "airports.csv"


class Region(str, Enum):
    EUROPE = "europe"
    NORTH_AFRICA = "north_africa"
    MIDDLE_EAST = "middle_east"
    SUB_SAHARAN_AFRICA = "sub_saharan_africa"
    ASIA = "asia"
    NORTH_AMERICA = "north_america"
    CARIBBEAN_CENTRAL_AMERICA = "caribbean_central_america"
    SOUTH_AMERICA = "south_america"
    OCEANIA = "oceania"
    UNKNOWN = "unknown"


# Flights longer than this (great-circle km from the origin) count as long-haul.
# Covers all of Europe incl. the Canaries, North Africa and the Levant as short/medium-haul.
LONG_HAUL_MIN_KM = 3500.0

# Effective block speed (incl. routing and taxi) used for flight-time estimates.
_EFFECTIVE_KMH = 800.0
_FIXED_OVERHEAD_H = 0.5
_UNKNOWN_FLIGHT_HOURS = 5.0

_EUROPE_COUNTRIES = {
    "AD", "AL", "AM", "AT", "AX", "AZ", "BA", "BE", "BG", "BY", "CH", "CY", "CZ", "DE", "DK", "EE",
    "ES", "FI", "FO", "FR", "GB", "GE", "GG", "GI", "GR", "HR", "HU", "IE", "IM", "IS", "IT", "JE",
    "LI", "LT", "LU", "LV", "MC", "MD", "ME", "MK", "MT", "NL", "NO", "PL", "PT", "RO", "RS", "SE",
    "SI", "SJ", "SK", "SM", "TR", "UA", "VA", "XK",
}
_NORTH_AFRICA_COUNTRIES = {"DZ", "EG", "EH", "LY", "MA", "TN"}
_MIDDLE_EAST_COUNTRIES = {
    "AE", "BH", "IL", "IQ", "IR", "JO", "KW", "LB", "OM", "PS", "QA", "SA", "SY", "YE",
}
_CARIBBEAN_CENTRAL_AMERICA_COUNTRIES = {
    "AG", "AI", "AW", "BB", "BL", "BQ", "BS", "BZ", "CR", "CU", "CW", "DM", "DO", "GD", "GP", "GT",
    "HN", "HT", "JM", "KN", "KY", "LC", "MF", "MQ", "MS", "NI", "PA", "PR", "SV", "SX", "TC", "TT",
    "VC", "VG", "VI",
}
_CONTINENT_REGION = {
    "EU": Region.EUROPE,
    "AS": Region.ASIA,
    "AF": Region.SUB_SAHARAN_AFRICA,
    "NA": Region.NORTH_AMERICA,
    "SA": Region.SOUTH_AMERICA,
    "OC": Region.OCEANIA,
    "AN": Region.OCEANIA,
}


@dataclass(frozen=True)
class Cluster:
    name: str
    city_code: str
    airports: Tuple[str, ...]


# Airports serving the same city. city_code is the IATA metropolitan-area code.
CLUSTERS: Dict[str, Cluster] = {c.name: c for c in [
    Cluster("Milan", "MIL", ("MXP", "LIN", "BGY")),
    Cluster("London", "LON", ("LHR", "LGW", "STN", "LTN", "LCY", "SEN")),
    Cluster("Paris", "PAR", ("CDG", "ORY", "BVA")),
    Cluster("Rome", "ROM", ("FCO", "CIA")),
    Cluster("Venice", "VCE", ("VCE", "TSF")),
    Cluster("Barcelona", "BCN", ("BCN", "GRO", "REU")),
    Cluster("Brussels", "BRU", ("BRU", "CRL")),
    Cluster("Frankfurt", "FRA", ("FRA", "HHN")),
    Cluster("Warsaw", "WAW", ("WAW", "WMI")),
    Cluster("Stockholm", "STO", ("ARN", "BMA", "NYO")),
    Cluster("Oslo", "OSL", ("OSL", "TRF")),
    Cluster("Copenhagen", "CPH", ("CPH", "MMX")),
    Cluster("Istanbul", "IST", ("IST", "SAW")),
    Cluster("New York", "NYC", ("JFK", "EWR", "LGA")),
    Cluster("Chicago", "CHI", ("ORD", "MDW")),
    Cluster("Washington", "WAS", ("IAD", "DCA", "BWI")),
    Cluster("Tokyo", "TYO", ("NRT", "HND")),
    Cluster("Seoul", "SEL", ("ICN", "GMP")),
    Cluster("Bangkok", "BKK", ("BKK", "DMK")),
    Cluster("Buenos Aires", "BUE", ("EZE", "AEP")),
    Cluster("São Paulo", "SAO", ("GRU", "CGH", "VCP")),
]}

CLUSTER_OF: Dict[str, str] = {
    airport: cluster.name for cluster in CLUSTERS.values() for airport in cluster.airports
}

# City names for common airports; the dataset's municipality is often a suburb.
_CITY_OVERRIDES: Dict[str, str] = {
    "VRN": "Verona", "BLQ": "Bologna", "NAP": "Naples", "TRN": "Turin", "PSA": "Pisa",
    "FLR": "Florence", "CTA": "Catania", "PMO": "Palermo", "BRI": "Bari", "CAG": "Cagliari",
    "OLB": "Olbia", "AHO": "Alghero", "GOA": "Genoa", "TRS": "Trieste",
    "AMS": "Amsterdam", "MAD": "Madrid", "DUB": "Dublin", "KRK": "Krakow", "PRG": "Prague",
    "BUD": "Budapest", "LIS": "Lisbon", "OPO": "Porto", "FAO": "Faro", "ATH": "Athens",
    "HEL": "Helsinki", "VIE": "Vienna", "ZRH": "Zurich", "GVA": "Geneva", "BSL": "Basel",
    "EDI": "Edinburgh", "GLA": "Glasgow", "MAN": "Manchester", "BHX": "Birmingham",
    "NCE": "Nice", "MRS": "Marseille", "LYS": "Lyon", "TLS": "Toulouse", "BOD": "Bordeaux",
    "SVQ": "Seville", "VLC": "Valencia", "AGP": "Malaga", "ALC": "Alicante", "BIO": "Bilbao",
    "MAH": "Menorca", "IBZ": "Ibiza", "PMI": "Palma de Mallorca", "TFS": "Tenerife",
    "TFN": "Tenerife", "ACE": "Lanzarote", "LPA": "Gran Canaria", "FUE": "Fuerteventura",
    "MUC": "Munich", "BER": "Berlin", "HAM": "Hamburg", "DUS": "Dusseldorf", "CGN": "Cologne",
    "STR": "Stuttgart", "SKG": "Thessaloniki", "RHO": "Rhodes", "CFU": "Corfu", "HER": "Heraklion",
    "CHQ": "Chania", "JTR": "Santorini", "JMK": "Mykonos", "SPU": "Split", "DBV": "Dubrovnik",
    "ZAG": "Zagreb", "BEG": "Belgrade", "SOF": "Sofia", "OTP": "Bucharest", "RIX": "Riga",
    "TLL": "Tallinn", "VNO": "Vilnius", "MLA": "Malta", "LCA": "Larnaca", "PFO": "Paphos",
    "TLV": "Tel Aviv", "AMM": "Amman", "CAI": "Cairo", "RAK": "Marrakech", "AGA": "Agadir",
    "DXB": "Dubai", "AUH": "Abu Dhabi", "DOH": "Doha",
    "LAX": "Los Angeles", "MIA": "Miami", "BOS": "Boston", "SFO": "San Francisco",
    "YYZ": "Toronto", "YVR": "Vancouver", "YUL": "Montreal", "CUN": "Cancun", "MEX": "Mexico City",
    "HKG": "Hong Kong", "SIN": "Singapore", "KUL": "Kuala Lumpur", "CGK": "Jakarta",
    "DPS": "Bali", "HKT": "Phuket", "DEL": "Delhi", "BOM": "Mumbai", "MLE": "Male",
    "BOG": "Bogota", "LIM": "Lima", "SCL": "Santiago", "GIG": "Rio de Janeiro",
    "JNB": "Johannesburg", "CPT": "Cape Town", "NBO": "Nairobi", "ZNZ": "Zanzibar",
    "SYD": "Sydney", "MEL": "Melbourne", "AKL": "Auckland",
    # OurAirports gives the municipality an airport sits in, often a suburb or village:
    # use the city travellers (and the airlines) know. Found by diffing against the
    # city names Ryanair's fare API returns and by scanning Europe / North Africa.
    "RZE": "Rzeszów", "TIA": "Tirana", "FEZ": "Fez", "BEM": "Beni Mellal", "NDR": "Nador",
    "OUD": "Oujda", "CRV": "Crotone", "AOI": "Ancona", "CUF": "Cuneo", "VBS": "Brescia",
    "EMA": "East Midlands", "MME": "Teesside", "KIR": "Kerry", "NOC": "Knock",
    "IOM": "Isle of Man", "JER": "Jersey", "GCI": "Guernsey", "EFL": "Kefalonia", "JSH": "Sitia",
    "LDE": "Lourdes", "SPC": "La Palma", "TER": "Terceira", "PXO": "Porto Santo",
    "LIL": "Lille", "LGG": "Liège", "LUG": "Lugano", "LJU": "Ljubljana", "LEJ": "Leipzig",
    "LEN": "León", "LCG": "A Coruña", "OVD": "Asturias", "EAS": "San Sebastián", "VIT": "Vitoria",
    "RMU": "Murcia", "RNS": "Rennes", "TLN": "Toulon", "ETZ": "Metz", "OST": "Ostend",
    "FKB": "Karlsruhe", "FMO": "Münster", "PAD": "Paderborn", "KSF": "Kassel", "GRZ": "Graz",
    "BNX": "Banja Luka", "TZL": "Tuzla", "SKP": "Skopje", "PRN": "Pristina", "BWK": "Brač",
    "OSR": "Ostrava", "SZY": "Olsztyn", "IEG": "Zielona Góra", "TGM": "Târgu Mureș",
    "BAY": "Baia Mare", "SOB": "Hévíz–Balaton", "JYV": "Jyväskylä", "HAU": "Haugesund",
    "RNN": "Bornholm", "ADB": "Izmir", "COV": "Adana", "KUT": "Kutaisi", "DJE": "Djerba",
}

BEACH_AIRPORTS = frozenset({
    # Mediterranean & Atlantic islands and coasts
    "PMI", "IBZ", "MAH", "TFS", "TFN", "ACE", "LPA", "FUE", "GRO", "AGP", "ALC", "FAO",
    "RHO", "CFU", "HER", "CHQ", "JTR", "JMK", "KGS", "ZTH", "EFL", "SKG", "PVK",
    "OLB", "CAG", "AHO", "BDS", "SPU", "DBV", "ZAD", "PUY", "MLA", "PFO", "LCA",
    "DLM", "BJV", "AYT", "AGA", "RAK", "HRG", "SSH", "RMF", "DJE", "MIR",
    # Caribbean / Atlantic
    "CUN", "PUJ", "MBJ", "BGI", "AUA", "SXM", "FDF", "PTP", "SDQ", "HAV", "VRA", "NAS",
    # Indian Ocean & Africa
    "MLE", "RUN", "SEZ", "MRU", "ZNZ",
    # Asia beach
    "HKT", "USM", "DPS", "BKI", "KBV", "CMB", "GOI", "PQC", "CXR",
})


@dataclass(frozen=True)
class Airport:
    iata: str
    name: str
    city: str
    country: str
    continent: str
    lat: Optional[float]
    lon: Optional[float]
    size: str  # "L" large, "M" medium, "?" learned at runtime

    @property
    def region(self) -> Region:
        return _region_for(self.country, self.continent)

    @property
    def cluster(self) -> Optional[str]:
        return CLUSTER_OF.get(self.iata)

    @property
    def is_beach(self) -> bool:
        return self.iata in BEACH_AIRPORTS


def _region_for(country: str, continent: str) -> Region:
    if country in _EUROPE_COUNTRIES:
        return Region.EUROPE
    if country in _NORTH_AFRICA_COUNTRIES:
        return Region.NORTH_AFRICA
    if country in _MIDDLE_EAST_COUNTRIES:
        return Region.MIDDLE_EAST
    if country in _CARIBBEAN_CENTRAL_AMERICA_COUNTRIES:
        return Region.CARIBBEAN_CENTRAL_AMERICA
    return _CONTINENT_REGION.get(continent, Region.UNKNOWN)


def _clean_city(raw: str) -> str:
    """
    OurAirports municipality → a display city:
    "Ferno (VA)" → "Ferno", "Leeds, West Yorkshire" → "Leeds",
    "Limoges/Bellegarde" → "Limoges", "Kos Island" → "Kos".
    """
    city = re.sub(r"\s*\(.*?\)\s*", " ", raw).strip()
    city = city.split(",")[0].strip()
    city = re.split(r"\s*/\s*", city)[0].strip()
    return re.sub(r"\s+Island$", "", city)


@lru_cache(maxsize=1)
def _load() -> Dict[str, Airport]:
    airports: Dict[str, Airport] = {}
    with _DATA_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            iata = row["iata"]
            city = CLUSTER_OF.get(iata) or _CITY_OVERRIDES.get(iata) or _clean_city(row["city"]) or row["name"]
            airports[iata] = Airport(
                iata=iata,
                name=row["name"],
                city=city,
                country=row["country"],
                continent=row["continent"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                size=row["size"],
            )
    return airports


def _norm(code: str) -> str:
    return (code or "").strip().upper()


def get(iata: str) -> Optional[Airport]:
    return _load().get(_norm(iata))


def is_known(iata: str) -> bool:
    return _norm(iata) in _load()


def learn(
    iata: str,
    city: Optional[str] = None,
    country: Optional[str] = None,
    name: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> Airport:
    """
    Register (or enrich) an airport reported by a data source.
    Known airports keep their curated city name and coordinates; unknown
    ones are added so city/region lookups work for the rest of the run.
    """
    code = _norm(iata)
    registry = _load()
    existing = registry.get(code)
    if existing:
        if existing.lat is None and lat is not None and lon is not None:
            registry[code] = replace(existing, lat=lat, lon=lon)
        return registry[code]
    country_code = _norm(country or "")
    airport = Airport(
        iata=code,
        name=name or city or code,
        city=CLUSTER_OF.get(code) or _CITY_OVERRIDES.get(code) or city or code,
        country=country_code,
        continent="EU" if country_code in _EUROPE_COUNTRIES else "",
        lat=lat,
        lon=lon,
        size="?",
    )
    registry[code] = airport
    _city_index.cache_clear()
    return airport


def city_of(iata: str) -> str:
    """Human city name for an airport; falls back to the code itself."""
    code = _norm(iata)
    if code in CLUSTER_OF:
        return CLUSTER_OF[code]
    airport = get(code)
    return airport.city if airport else code


def region_of(iata: str) -> Region:
    airport = get(iata)
    return airport.region if airport else Region.UNKNOWN


def is_europe(iata: str) -> bool:
    return region_of(iata) == Region.EUROPE


def is_beach(iata: str) -> bool:
    return _norm(iata) in BEACH_AIRPORTS


def distance_km(a: str, b: str) -> Optional[float]:
    """Great-circle distance between two airports, or None if either has no coordinates."""
    pa, pb = get(a), get(b)
    if not pa or not pb or pa.lat is None or pb.lat is None:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (pa.lat, pa.lon, pb.lat, pb.lon))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


_SHORT_HAUL_REGIONS = {Region.EUROPE, Region.NORTH_AFRICA}


def is_long_haul(destination: str, origin: Optional[str] = None) -> bool:
    """
    True for long-haul destinations. Uses the great-circle distance from
    `origin` when both airports have coordinates, otherwise the region.
    """
    if origin:
        km = distance_km(origin, destination)
        if km is not None:
            return km > LONG_HAUL_MIN_KM
    region = region_of(destination)
    if region == Region.UNKNOWN:
        return False
    return region not in _SHORT_HAUL_REGIONS


# Typical one-way flight hours from a European origin, when no distance is available.
_REGION_FALLBACK_HOURS = {
    Region.EUROPE: 2.5,
    Region.NORTH_AFRICA: 3.5,
    Region.MIDDLE_EAST: 5.5,
    Region.SUB_SAHARAN_AFRICA: 10.0,
    Region.ASIA: 12.0,
    Region.NORTH_AMERICA: 10.0,
    Region.CARIBBEAN_CENTRAL_AMERICA: 11.0,
    Region.SOUTH_AMERICA: 12.5,
    Region.OCEANIA: 22.0,
}


def estimate_flight_hours(origin: str, destination: str) -> float:
    """Estimated one-way flight time in hours (distance-based, region fallback)."""
    if _norm(origin) == _norm(destination):
        return 0.0
    km = distance_km(origin, destination)
    if km is not None:
        return round(km / _EFFECTIVE_KMH + _FIXED_OVERHEAD_H, 1)
    return _REGION_FALLBACK_HOURS.get(region_of(destination), _UNKNOWN_FLIGHT_HOURS)


def _fold(text: str) -> str:
    """Lowercase and strip accents: 'Kraków' → 'krakow'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold().strip()


@lru_cache(maxsize=1)
def _city_index() -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for cluster in CLUSTERS.values():
        index.setdefault(_fold(cluster.name), []).extend(cluster.airports)
    size_rank = {"L": 0, "M": 1, "?": 2}
    for airport in sorted(_load().values(), key=lambda a: (size_rank.get(a.size, 3), a.iata)):
        key = _fold(airport.city)
        codes = index.setdefault(key, [])
        if airport.iata not in codes:
            codes.append(airport.iata)
    return index


def airports_in_city(city: str) -> List[str]:
    """IATA codes serving a city name (accent/case-insensitive), largest first."""
    return list(_city_index().get(_fold(city), []))


def iata_for_city(city: str) -> Optional[str]:
    codes = airports_in_city(city)
    return codes[0] if codes else None


def display_name(code: str) -> str:
    """City name for an airport code or a metropolitan-area code (MXP or MIL → "Milan")."""
    code = _norm(code)
    for cluster in CLUSTERS.values():
        if cluster.city_code == code:
            return cluster.name
    return city_of(code)


def search(query: str, limit: int = 8) -> List[Airport]:
    """
    Airports for an autocomplete box: an exact code first, then codes starting
    with the query, then cities and airport names starting with it (accents and
    case ignored), larger airports first.
    """
    q = _fold(query)
    if not q:
        return []
    size_rank = {"L": 0, "M": 1, "?": 2}
    scored = []
    for airport in _load().values():
        code = airport.iata.casefold()
        if code == q:
            rank = 0
        elif code.startswith(q):
            rank = 1
        elif _fold(airport.city).startswith(q):
            rank = 2
        elif _fold(airport.name).startswith(q) or f" {q}" in f" {_fold(airport.name)}":
            rank = 3
        else:
            continue
        scored.append((rank, size_rank.get(airport.size, 3), airport.city, airport.iata, airport))
    return [entry[-1] for entry in sorted(scored)[:limit]]
