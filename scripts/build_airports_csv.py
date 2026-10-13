"""
Regenerate utils/data/airports.csv and utils/data/countries.csv from the OurAirports dataset.

OurAirports data is public domain: https://ourairports.com/data/
Keeps large and medium airports that have scheduled service and an IATA code,
and the English name of each of their countries (Google Hotels needs "Naples,
Italy": "Naples" alone is Naples, Florida).

Usage:
    python scripts/build_airports_csv.py                               # download the latest dataset
    python scripts/build_airports_csv.py airports.csv [countries.csv]  # use local copies
"""
from __future__ import annotations

import csv
import io
import sys
import urllib.request
from pathlib import Path

SOURCE_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
COUNTRIES_URL = "https://davidmegginson.github.io/ourairports-data/countries.csv"
OUT_PATH = Path(__file__).resolve().parent.parent / "utils" / "data" / "airports.csv"
COUNTRIES_PATH = OUT_PATH.with_name("countries.csv")
KEEP_TYPES = {"large_airport": "L", "medium_airport": "M"}


def _read_source(arg: str | None, url: str = SOURCE_URL) -> str:
    if arg:
        return Path(arg).read_text(encoding="utf-8")
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8")


def write_countries(text: str, codes: set[str]) -> int:
    """utils/data/countries.csv: code and English name of each country the airports are in."""
    rows = [{"code": r["code"].strip().upper(), "name": r["name"].strip()} for r in csv.DictReader(io.StringIO(text))]
    rows = sorted((r for r in rows if r["code"] in codes), key=lambda r: r["code"])
    with COUNTRIES_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "name"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    text = _read_source(sys.argv[1] if len(sys.argv) > 1 else None)
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        iata = (r.get("iata_code") or "").strip().upper()
        if len(iata) != 3 or r["type"] not in KEEP_TYPES or r["scheduled_service"] != "yes":
            continue
        rows.append({
            "iata": iata,
            "name": r["name"].strip(),
            "city": (r["municipality"] or "").strip(),
            "country": r["iso_country"].strip().upper(),
            "continent": r["continent"].strip().upper(),
            "lat": f"{float(r['latitude_deg']):.4f}",
            "lon": f"{float(r['longitude_deg']):.4f}",
            "size": KEEP_TYPES[r["type"]],
        })
    # Prefer large airports when an IATA code appears twice.
    rows.sort(key=lambda x: (x["iata"], x["size"]))
    seen: set[str] = set()
    unique = []
    for row in rows:
        if row["iata"] not in seen:
            seen.add(row["iata"])
            unique.append(row)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(unique[0].keys()))
        writer.writeheader()
        writer.writerows(unique)
    print(f"Wrote {len(unique)} airports to {OUT_PATH}")
    countries = _read_source(sys.argv[2] if len(sys.argv) > 2 else None, COUNTRIES_URL)
    print(f"Wrote {write_countries(countries, {r['country'] for r in unique})} countries to {COUNTRIES_PATH}")


if __name__ == "__main__":
    main()
