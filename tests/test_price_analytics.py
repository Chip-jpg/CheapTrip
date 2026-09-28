"""Price history and anomaly detection (B16): keyed baselines from earlier cycles only."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import aiosqlite
import pytest

from storage.database import get_db_path, get_pending_instant_alerts, init_db, purge_old_rows, save_deal
from storage.models import AlertTier, DealType, FlightLeg, Trip
from storage.price_analytics import (
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


def _history(key: PriceKey, prices_per_cycle):
    return [(key, price, f"cycle{i}") for i, prices in enumerate(prices_per_cycle) for price in prices]


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
