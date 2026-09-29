"""Price history and anomaly detection (B16): keyed baselines from earlier cycles only."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import aiosqlite
import pytest

from storage.database import get_db_path, get_pending_instant_alerts, init_db, purge_old_rows, save_deal
from storage.models import AlertTier, DealType, FlightLeg, Trip
from storage.price_analytics import (
    ERROR_FARE_MIN_DAYS,
    MIN_SAMPLES,
    PriceIndex,
    PriceKey,
    price_key,
    record_fares,
)
from trip_builder.builder import build_trips

DEP = date(2026, 11, 13)


def _leg(price: float = 60.0, origin: str = "MXP", dest: str = "KRK", nights: Optional[int] = 3,
         dep: date = DEP, **extra) -> FlightLeg:
    return FlightLeg(
        origin=origin, destination=dest, price_eur=price, departure_date=dep,
        return_date=dep + timedelta(days=nights) if nights else None, is_round_trip=bool(nights),
        source="ryanair", booking_url="https://example.com/book", **extra,
    )


def _history(key: PriceKey, prices_per_cycle, days_apart: int = 0):
    """One cycle per list; cycle i is recorded `i * days_apart` days after the first."""
    return [
        (key, price, f"cycle{i}", (date(2026, 9, 1) + timedelta(days=i * days_apart)).isoformat())
        for i, prices in enumerate(prices_per_cycle) for price in prices
    ]


KEY = price_key(_leg())


# ── Keys ──────────────────────────────────────────────────────────────────────

def test_key_groups_city_trip_type_length_and_month():
    assert KEY == PriceKey("MIL", "KRK", "rt", "weekend", "2026-11")
    # Milan's airports share one history
    assert price_key(_leg(origin="BGY")) == KEY
    assert price_key(_leg(nights=6)).nights_bucket == "short"
    assert price_key(_leg(nights=None)) == PriceKey("MIL", "KRK", "ow", "ow", "2026-11")


def test_feed_posts_and_undated_fares_have_no_key():
    assert price_key(_leg(is_feed_deal=True)) is None
    assert price_key(FlightLeg(origin="MXP", destination="KRK", price_eur=20.0)) is None


# ── Baselines ─────────────────────────────────────────────────────────────────

def test_no_baseline_without_enough_history():
    few = PriceIndex(_history(KEY, [[60.0, 62.0], [58.0]]))
    assert few.baseline(_leg()) is None
    one_cycle = PriceIndex(_history(KEY, [[60.0] * (MIN_SAMPLES + 4)]))
    assert one_cycle.baseline(_leg()) is None, "a single cycle is not a history"


def test_fare_far_below_the_usual_price_is_an_anomaly():
    index = PriceIndex(_history(KEY, [[60.0, 62.0, 58.0], [60.0, 61.0, 59.0]]))

    result = index.check(_leg(price=35.0))

    assert result.is_anomaly
    assert result.normal_price == 60.0
    assert result.deviation_pct == pytest.approx(-41.7, abs=0.1)
    assert "42% below the usual €60 (weekend trips, November)" in result.description


def test_ordinary_fare_is_not_an_anomaly_but_gets_a_normal_price():
    index = PriceIndex(_history(KEY, [[60.0, 62.0, 58.0], [60.0, 61.0, 59.0]]))

    result = index.check(_leg(price=57.0))

    assert not result.is_anomaly
    assert result.normal_price == 60.0


def test_new_low_is_an_anomaly():
    index = PriceIndex(_history(KEY, [[60.0, 62.0, 58.0], [60.0, 61.0, 59.0]]))

    result = index.check(_leg(price=54.0))  # 10% under the median, but under 58 × 0.95

    assert result.is_anomaly and result.is_all_time_low
    assert "new low" in result.description


def test_other_lengths_and_months_have_their_own_baseline():
    index = PriceIndex(_history(KEY, [[60.0, 62.0, 58.0], [60.0, 61.0, 59.0]]))

    assert index.baseline(_leg(nights=6)) is None
    assert index.baseline(_leg(dep=date(2026, 12, 11))) is None


# ── Possible error fares (B28) ────────────────────────────────────────────────

JFK = dict(dest="JFK", nights=10)
JFK_KEY = price_key(_leg(**JFK))
JFK_HISTORY = [[540.0, 550.0, 560.0, 545.0, 555.0, 550.0, 552.0]] * 3  # 21 fares, median €550


def test_cheap_short_haul_fare_is_unusually_cheap_not_an_error_fare():
    # Round 3 dry run: Berlin at €30 against a usual €79 was labelled a possible error fare
    berlin = PriceIndex(_history(price_key(_leg(dest="BER")), [[78.0, 79.0, 80.0] * 3] * 3, days_apart=2))

    result = berlin.check(_leg(dest="BER", price=30.0))

    assert result.is_anomaly and not result.is_possible_error_fare


def test_far_below_a_well_established_price_is_a_possible_error_fare():
    index = PriceIndex(_history(JFK_KEY, JFK_HISTORY, days_apart=1))

    result = index.check(_leg(price=150.0, **JFK))

    assert result.is_anomaly and result.is_possible_error_fare


def test_young_or_thin_history_is_not_enough_for_an_error_fare():
    same_day = PriceIndex(_history(JFK_KEY, JFK_HISTORY))
    two_days = PriceIndex(_history(JFK_KEY, JFK_HISTORY[:ERROR_FARE_MIN_DAYS - 1], days_apart=1))
    few_fares = PriceIndex(_history(JFK_KEY, [[550.0, 552.0]] * 3, days_apart=1))

    for index in (same_day, two_days, few_fares):
        result = index.check(_leg(price=150.0, **JFK))
        assert result.is_anomaly and not result.is_possible_error_fare


def test_error_fare_needs_a_fare_far_under_the_usual_price():
    index = PriceIndex(_history(JFK_KEY, JFK_HISTORY, days_apart=1))

    assert not index.check(_leg(price=200.0, **JFK)).is_possible_error_fare  # 36% of €550


# ── Through the database and the builder ──────────────────────────────────────

async def test_first_cycle_has_no_anomalies(engine):
    await init_db()
    trips = await build_trips([_leg(price=p) for p in (30.0, 35.0, 40.0)], [])

    assert not any(t.is_anomaly for t in trips)
    assert all(t.normal_price_eur is None for t in trips)


async def test_current_cycle_never_forms_its_own_baseline(engine):
    await init_db()
    await record_fares([_leg(price=60.0 + i) for i in range(10)], "this-cycle")
    await record_fares([_leg(price=60.0 + i) for i in range(10)], "this-cycle")

    index = await PriceIndex.load(exclude_cycle="this-cycle")

    assert index.baseline(_leg()) is None


async def test_drop_after_three_cycles_is_flagged(engine):
    await init_db()
    for _ in range(3):
        await build_trips([_leg(price=p) for p in (58.0, 60.0, 62.0)], [])

    [trip] = await build_trips([_leg(price=35.0)], [])

    assert trip.is_anomaly
    assert trip.normal_price_eur == 60.0
    assert trip.discount_pct == pytest.approx(41.7, abs=0.1)
    assert "below the usual €60" in trip.anomaly_description


async def _record_jfk_history(days_apart: int) -> None:
    for i, prices in enumerate(JFK_HISTORY):
        await record_fares([_leg(price=p, **JFK) for p in prices], f"c{i}")
    async with aiosqlite.connect(await get_db_path()) as db:
        for i in range(len(JFK_HISTORY)):
            day = (datetime.utcnow() - timedelta(days=(len(JFK_HISTORY) - i) * days_apart)).isoformat()
            await db.execute("UPDATE price_history SET recorded_at = ? WHERE cycle_id = ?", (day, f"c{i}"))
        await db.commit()


async def test_error_fare_label_uses_history_spread_over_days(engine):
    await init_db()
    await _record_jfk_history(days_apart=1)

    [trip] = await build_trips([_leg(price=150.0, **JFK)], [])

    assert trip.is_error_fare and trip.deal_type == DealType.ERROR_FARE
    assert trip.is_anomaly


async def test_hours_of_history_make_an_anomaly_but_no_error_fare(engine):
    await init_db()
    await _record_jfk_history(days_apart=0)

    [trip] = await build_trips([_leg(price=150.0, **JFK)], [])

    assert trip.is_anomaly
    assert not trip.is_error_fare and trip.deal_type == DealType.FLIGHT_ONLY


async def test_source_flag_still_marks_an_error_fare(engine):
    await init_db()

    [trip] = await build_trips([_leg(price=150.0, is_error_fare_hint=True, **JFK)], [])

    assert trip.is_error_fare and trip.deal_type == DealType.ERROR_FARE


async def test_feed_deals_stay_out_of_the_history(engine):
    await init_db()
    recorded = await record_fares([_leg(is_feed_deal=True), _leg()], "c1")
    assert recorded == 1


async def test_rows_from_before_the_keyed_schema_are_ignored(engine):
    await init_db()
    async with aiosqlite.connect(await get_db_path()) as db:
        for i in range(10):
            await db.execute(
                "INSERT INTO price_history (route, price_eur, source, recorded_at) VALUES (?, ?, ?, ?)",
                ("MXP-KRK", 60.0, "old", datetime.utcnow().isoformat()),
            )
        await db.commit()

    index = await PriceIndex.load(exclude_cycle="now")

    assert index.baseline(_leg()) is None


async def test_init_db_adds_key_columns_to_an_old_database(engine):
    async with aiosqlite.connect(await get_db_path()) as db:
        await db.execute(
            "CREATE TABLE price_history (id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL, "
            "price_eur REAL NOT NULL, source TEXT NOT NULL, recorded_at TEXT NOT NULL)"
        )
        await db.commit()

    await init_db()
    await record_fares([_leg()], "c1")

    async with aiosqlite.connect(await get_db_path()) as db:
        row = await (await db.execute("SELECT origin_city, nights_bucket, cycle_id FROM price_history")).fetchone()
    assert row == ("MIL", "weekend", "c1")


# ── Retention and sending order ───────────────────────────────────────────────

def _trip(hash_: str, discount: Optional[float], cost: float = 50.0) -> Trip:
    leg = _leg(price=cost)
    return Trip(
        hash=hash_, trip_id=hash_, deal_type=DealType.FLIGHT_ONLY, route="Milan → Krakow",
        outbound_flight=leg, flight_cost_eur=cost, total_cost_eur=cost, discount_pct=discount,
        alert_tier=AlertTier.INSTANT, data_confidence_score=0.9,
    )


async def test_retention_purges_old_rows(engine):
    await init_db()
    old = (datetime.utcnow() - timedelta(days=200)).isoformat()
    await record_fares([_leg()], "fresh")
    await save_deal(_trip("keep", 10.0))
    await save_deal(_trip("gone", 10.0))
    async with aiosqlite.connect(await get_db_path()) as db:
        await db.execute(
            "INSERT INTO price_history (route, price_eur, source, recorded_at) VALUES ('MXP-KRK', 50, 'x', ?)", (old,)
        )
        await db.execute("UPDATE deals SET expires_at = ? WHERE hash = 'gone'", (old,))
        await db.commit()

    purged = await purge_old_rows()

    assert purged["price_history"] == 1 and purged["deals"] == 1
    async with aiosqlite.connect(await get_db_path()) as db:
        hashes = [r[0] for r in await (await db.execute("SELECT hash FROM deals")).fetchall()]
    assert hashes == ["keep"]


async def test_pending_instants_go_out_best_discount_first(engine):
    await init_db()
    await save_deal(_trip("cheap-no-discount", None, cost=20.0))
    await save_deal(_trip("big-discount", 55.0, cost=45.0))
    await save_deal(_trip("small-discount", 36.0, cost=40.0))

    pending = await get_pending_instant_alerts(limit=10)

    assert [t.hash for t in pending] == ["big-discount", "small-discount", "cheap-no-discount"]
