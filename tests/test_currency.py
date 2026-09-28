"""Tests for currency conversion."""
from __future__ import annotations

import pytest

from normalizers.currency import CurrencyConverter, UnknownCurrencyError


class TestCurrencyConverter:
    def test_eur_passthrough(self):
        c = CurrencyConverter()
        assert c.to_eur(100.0, "EUR") == 100.0

    def test_usd_to_eur(self):
        c = CurrencyConverter()
        result = c.to_eur(108.0, "USD")
        # At fallback rate 1 EUR = 1.08 USD → 108 USD = 100 EUR
        assert abs(result - 100.0) < 2.0

    def test_gbp_to_eur(self):
        c = CurrencyConverter()
        result = c.to_eur(86.0, "GBP")
        # At fallback rate 1 EUR = 0.86 GBP → 86 GBP ≈ 100 EUR
        assert abs(result - 100.0) < 2.0

    def test_unknown_currency_is_an_error(self):
        # Used to be treated as EUR silently (100 XYZ → €100)
        c = CurrencyConverter()
        assert not c.supports("XYZ")
        with pytest.raises(UnknownCurrencyError):
            c.to_eur(100.0, "XYZ")

    def test_rounding_to_2dp(self):
        c = CurrencyConverter()
        result = c.to_eur(100.0, "EUR")
        assert result == round(result, 2)


async def test_results_in_unknown_currencies_are_skipped(engine):
    from normalizers.flight import normalize_flights
    from tests.harness import flight

    legs = await normalize_flights([flight(destination="KRK", price=40.0), flight(destination="PRG", currency="XYZ")])

    assert [leg.destination for leg in legs] == ["KRK"]
