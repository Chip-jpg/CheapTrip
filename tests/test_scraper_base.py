"""Shared scraper HTTP setup: every advertised content encoding must be decodable."""
from __future__ import annotations

import gzip
import zlib
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from scrapers import google_flights
from scrapers.base import BASE_HEADERS, ScrapeStatus
from scrapers.google_flights import GoogleFlightsScraper
from storage.models import SearchTask

FIXTURES = Path(__file__).parent / "fixtures"


def _compress(data: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        return gzip.compress(data)
    if encoding == "deflate":
        return zlib.compress(data)
    if encoding == "br":
        import brotli  # only advertised when installed
        return brotli.compress(data)
    raise AssertionError(f"advertised encoding with no test compressor: {encoding}")


ADVERTISED = [e.strip() for e in BASE_HEADERS["Accept-Encoding"].split(",")]


def test_brotli_is_advertised_when_installed():
    # requirements.txt installs httpx[brotli]; browsers send br, so should we
    assert "br" in ADVERTISED


@pytest.mark.parametrize("encoding", ADVERTISED)
async def test_scrapers_decode_every_advertised_encoding(monkeypatch, encoding):
    """Regression: 'br' was advertised without a decoder, so Google pages parsed as garbage."""
    async def _no_pause(_):
        return None
    monkeypatch.setattr(google_flights, "_pause", _no_pause)

    html = (FIXTURES / "google_flights_mxp_krk_rt.html").read_bytes()
    task = SearchTask(origin="MXP", destination="KRK", depart_from=date(2026, 11, 6),
                      depart_to=date(2026, 11, 6), nights_min=2, nights_max=2)
    with respx.mock() as router:
        router.get(url__startswith="https://www.google.com/travel/flights").mock(return_value=httpx.Response(
            200, content=_compress(html, encoding),
            headers={"content-encoding": encoding, "content-type": "text/html; charset=utf-8"}))
        outcome = await GoogleFlightsScraper().safe_scrape([task])
    assert outcome.status == ScrapeStatus.OK and outcome.count == 3
