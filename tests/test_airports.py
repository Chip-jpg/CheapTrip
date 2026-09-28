"""Tests for the airport registry."""
from __future__ import annotations

import pytest

from config import ALL_ORIGIN_AIRPORTS, POPULAR_DESTINATIONS
from utils import airports
from utils.airports import Region


@pytest.mark.parametrize("code", ["STN", "LTN", "ORY", "GRO", "BMA", "TRF", "BVA", "CRL", "WMI"])
def test_secondary_european_airports_are_europe(code):
    # Previously missing from the hard-filter Europe set → treated as long-haul.
    assert airports.is_europe(code)
    assert not airports.is_long_haul(code, "MXP")


@pytest.mark.parametrize("code", ["TFS", "LPA", "ACE", "FUE"])
def test_canary_islands_are_europe(code):
    # OurAirports lists the Canaries under Africa; they are Spain.
    assert airports.region_of(code) == Region.EUROPE


def test_regions():
    assert airports.region_of("RAK") == Region.NORTH_AFRICA
    assert airports.region_of("DXB") == Region.MIDDLE_EAST
    assert airports.region_of("JFK") == Region.NORTH_AMERICA
    assert airports.region_of("PUJ") == Region.CARIBBEAN_CENTRAL_AMERICA
    assert airports.region_of("NRT") == Region.ASIA
    assert airports.region_of("JNB") == Region.SUB_SAHARAN_AFRICA
    assert airports.region_of("SYD") == Region.OCEANIA
    assert airports.region_of("GRU") == Region.SOUTH_AMERICA


def test_long_haul_by_distance():
    assert airports.is_long_haul("JFK", "MXP")
    assert airports.is_long_haul("DXB", "MXP")
    assert not airports.is_long_haul("KRK", "MXP")
    assert not airports.is_long_haul("TLV", "MXP")  # ~2,900 km
    assert not airports.is_long_haul("TFS", "MXP")  # ~3,300 km


def test_long_haul_without_origin_uses_region():
    assert airports.is_long_haul("NRT")
    assert not airports.is_long_haul("PRG")
    assert not airports.is_long_haul("XYZ")  # unknown → not long-haul


def test_distance_sanity():
    km = airports.distance_km("MXP", "JFK")
    assert km is not None and 6200 < km < 6600
    assert airports.distance_km("MXP", "XYZ") is None


def test_flight_hours():
    assert airports.estimate_flight_hours("MXP", "MXP") == 0.0
    assert 1.0 < airports.estimate_flight_hours("MXP", "KRK") < 2.5
    assert 8.0 < airports.estimate_flight_hours("MXP", "JFK") < 10.0
    assert 11.0 < airports.estimate_flight_hours("MXP", "NRT") < 14.0
    assert airports.estimate_flight_hours("MXP", "SYD") <= 24.0
    assert airports.estimate_flight_hours("ZZZ", "YYY") > 0


def test_city_names_are_curated_not_suburbs():
    assert airports.city_of("MXP") == "Milan"
    assert airports.city_of("BGY") == "Milan"
    assert airports.city_of("KRK") == "Krakow"  # dataset municipality is "Balice"
    assert airports.city_of("BVA") == "Paris"
    assert airports.city_of("XYZ") == "XYZ"


def test_airports_in_city_is_accent_and_case_insensitive():
    assert "KRK" in airports.airports_in_city("Kraków")
    assert "KRK" in airports.airports_in_city("krakow")
    assert set(airports.airports_in_city("Milan")) >= {"MXP", "LIN", "BGY"}
    assert airports.airports_in_city("Atlantis") == []


def test_learn_unknown_airport():
    assert not airports.is_known("QQZ")
    learned = airports.learn("QQZ", city="Testville", country="gr")
    assert airports.is_known("QQZ")
    assert learned.city == "Testville"
    assert airports.region_of("QQZ") == Region.EUROPE
    assert "QQZ" in airports.airports_in_city("Testville")


def test_learn_known_airport_keeps_curated_city():
    assert airports.learn("KRK", city="Balice").city == "Krakow"


def test_every_configured_airport_is_known():
    missing = [c for c in set(ALL_ORIGIN_AIRPORTS + POPULAR_DESTINATIONS) if not airports.is_known(c)]
    assert missing == []


def test_every_cluster_airport_is_known():
    missing = [a for c in airports.CLUSTERS.values() for a in c.airports if not airports.is_known(a)]
    assert missing == []


def test_beach_airports():
    assert airports.is_beach("PMI")
    assert not airports.is_beach("PRG")


# ── Display city names (B25) ──────────────────────────────────────────────────

@pytest.mark.parametrize("iata,city", [
    # Suburbs / villages from OurAirports that showed up in the Round 1 dry run or
    # differed from Ryanair's own city names
    ("RZE", "Rzeszów"), ("TIA", "Tirana"), ("FEZ", "Fez"), ("BEM", "Beni Mellal"),
    ("CRV", "Crotone"), ("EMA", "East Midlands"), ("EFL", "Kefalonia"), ("LDE", "Lourdes"),
    ("AOI", "Ancona"), ("CUF", "Cuneo"), ("BNX", "Banja Luka"), ("FKB", "Karlsruhe"),
    ("LIL", "Lille"), ("LUG", "Lugano"), ("SPC", "La Palma"),
])
def test_curated_city_names(iata, city):
    assert airports.city_of(iata) == city


@pytest.mark.parametrize("iata,city", [
    ("LBA", "Leeds"),                 # "Leeds, West Yorkshire"
    ("NCL", "Newcastle upon Tyne"),   # "Newcastle upon Tyne, Tyne and Wear"
    ("LIG", "Limoges"),               # "Limoges/Bellegarde"
    ("TMP", "Tampere"),               # "Tampere / Pirkkala"
    ("KGS", "Kos"),                   # "Kos Island"
    ("SMI", "Samos"),                 # "Samos Island"
])
def test_municipality_suffixes_are_cleaned(iata, city):
    assert airports.city_of(iata) == city


def test_new_city_names_resolve_back_to_their_airport():
    assert airports.iata_for_city("Rzeszów") == "RZE"
    assert airports.iata_for_city("rzeszow") == "RZE"
    assert airports.iata_for_city("Tirana") == "TIA"
    assert airports.iata_for_city("Kos") == "KGS"
