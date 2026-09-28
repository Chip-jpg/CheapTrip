"""Tests for scheduler wiring and digest selection."""
from __future__ import annotations

from datetime import date

from scheduler.runner import create_scheduler, select_digest_trips
from storage.models import DealType, Trip


def _trip(route: str, cost: float, confidence: float = 0.8) -> Trip:
    return Trip(
        deal_type=DealType.FLIGHT_ONLY,
        route=route,
        total_cost_eur=cost,
        departure_date=date(2026, 11, 6),
        data_confidence_score=confidence,
    )


def test_digest_runs_in_configured_timezone(monkeypatch):
    from config import get_settings

    monkeypatch.setenv("TIMEZONE", "Europe/Rome")
    monkeypatch.setenv("DIGEST_HOUR", "8")
    get_settings.cache_clear()
    try:
        scheduler = create_scheduler()
        trigger = scheduler.get_job("daily_digest").trigger
        assert str(trigger.timezone) == "Europe/Rome"
        assert "hour='8'" in str(trigger)
    finally:
        get_settings.cache_clear()


def test_select_digest_keeps_cheapest_per_route():
    trips = [
        _trip("Milan → Krakow", 300.0),
        _trip("Milan → Krakow", 280.0),
        _trip("Milan → Prague", 310.0),
    ]
    selected = select_digest_trips(trips)
    assert [(t.route, t.total_cost_eur) for t in selected] == [
        ("Milan → Krakow", 280.0),
        ("Milan → Prague", 310.0),
    ]


def test_select_digest_orders_by_confidence_then_price_and_limits():
    trips = [_trip(f"Route {i}", 100.0 + i, confidence=0.5) for i in range(20)]
    trips.append(_trip("Best", 500.0, confidence=0.95))
    selected = select_digest_trips(trips, limit=5)
    assert len(selected) == 5
    assert selected[0].route == "Best"
    assert [t.total_cost_eur for t in selected[1:]] == [100.0, 101.0, 102.0, 103.0]
