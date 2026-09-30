"""Price history and anomaly detection (B16): keyed baselines from earlier cycles only."""
from __future__ import annotations

import os
import random
from dataclasses import astuple
from datetime import date, datetime, timedelta
from typing import Optional

import aiosqlite
import pytest

from storage.database import (
    get_db_path,
    get_pending_instant_alerts,
    get_state,
    init_db,
    purge_old_rows,
    save_deal,
)
from storage.models import AlertTier, DealType, FlightLeg, Trip
from storage.price_analytics import (
    BASELINE_WINDOW_DAYS,
    ERROR_FARE_MIN_DAYS,
    MIN_SAMPLES,
    Observation,
    PriceIndex,
    PriceKey,
    price_key,
    record_fares,
)
from trip_builder.builder import build_trips

DEP = date(2026, 11, 13)


def _leg(price: float = 60.0, origin: str = "MXP", dest: str = "KRK", nights: Optional[int] = 3,
         dep: date = DEP, source: str = "ryanair", **extra) -> FlightLeg:
    return FlightLeg(
        origin=origin, destination=dest, price_eur=price, departure_date=dep,
        return_date=dep + timedelta(days=nights) if nights else None, is_round_trip=bool(nights),
        source=source, booking_url="https://example.com/book", **extra,
    )


def _history(key: PriceKey, prices_per_cycle, days_apart: int = 0, depart: date = DEP):
    """One cycle per list, every price a different fare departing on `depart`;
    cycle i is recorded `i * days_apart` days after the first."""
    return [
        Observation(key, f"fare {i}-{j} {depart}", price, f"cycle{i}",
                    (date(2026, 9, 1) + timedelta(days=i * days_apart)).isoformat(), depart)
        for i, prices in enumerate(prices_per_cycle) for j, price in enumerate(prices)
    ]


def _seen(key: PriceKey, fares_per_cycle):
    """One cycle per dict of {fare: price}: the same fare can be seen in several cycles."""
    return [
        Observation(key, fare, price, f"cycle{i}", "2026-09-01", DEP)
        for i, fares in enumerate(fares_per_cycle) for fare, price in fares.items()
    ]


KEY = price_key(_leg())


# ── Keys ──────────────────────────────────────────────────────────────────────

def test_key_groups_city_trip_type_and_length():
    assert KEY == PriceKey("MIL", "KRK", "rt", "weekend")
    # Milan's airports share one history, and so do departure months (see the ±30-day window)
    assert price_key(_leg(origin="BGY")) == KEY
    assert price_key(_leg(dep=date(2027, 2, 5))) == KEY
    assert price_key(_leg(nights=6)).nights_bucket == "short"
    assert price_key(_leg(nights=None)) == PriceKey("MIL", "KRK", "ow", "ow")


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
    assert "42% below the usual €60 (weekend trips around 13 Nov)" in result.description


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


def test_a_fare_seen_in_several_cycles_counts_once():
    # Round 4 dry run: Lanzarote's 4 fares, the two expensive ones searched again in cycle 2,
    # made a "usual €205" and "€59 is 71% below usual"
    history = _seen(KEY, [
        {"ACE 28 Oct": 58.78, "ACE 21 Oct": 71.92, "ACE 14 Oct": 204.98, "ACE 7 Oct": 263.98},
        {"ACE 14 Oct": 204.98, "ACE 7 Oct": 263.98},
    ])

    assert PriceIndex(history).baseline(_leg()) is None  # 4 fares, not 6


def test_each_fare_counts_at_its_latest_price():
    history = _seen(KEY, [
        {"a": 60.0, "b": 62.0, "c": 58.0, "d": 100.0},
        {"e": 61.0, "f": 59.0, "d": 40.0},  # d dropped from €100 to €40
    ])

    base = PriceIndex(history).baseline(_leg())

    assert base.samples == 6 and base.low == 40.0
    assert base.median == 59.5  # 40, 58, 59, 60, 61, 62


def test_other_lengths_have_their_own_baseline():
    index = PriceIndex(_history(KEY, [[60.0, 62.0, 58.0], [60.0, 61.0, 59.0]]))

    assert index.baseline(_leg(nights=6)) is None


# ── The ±30-day window (B34) ──────────────────────────────────────────────────

def _around(*departures_and_prices):
    """Two cycles; each (departure, price) is a different fare seen in both."""
    return [
        Observation(KEY, f"MXP-KRK {dep}", price, f"cycle{c}", "2026-09-01", dep)
        for c in range(2) for dep, price in departures_and_prices
    ]


def test_fares_from_neighbouring_months_share_a_baseline():
    # Round 5 dry run: month keys left most fares with 2 comparable fares
    history = _around(
        (date(2026, 10, 20), 60.0), (date(2026, 10, 30), 62.0), (date(2026, 11, 6), 58.0),
        (date(2026, 11, 20), 61.0), (date(2026, 12, 4), 59.0), (date(2026, 12, 12), 60.0),
    )

    base = PriceIndex(history).baseline(_leg())  # 13 Nov

    assert base.samples == 6 and base.median == 60.0


def test_fares_more_than_30_days_away_are_not_compared():
    window = timedelta(days=BASELINE_WINDOW_DAYS)
    history = _around(
        *[(DEP + timedelta(days=d), 60.0) for d in (-10, -5, 5, 10, 20)],  # 5 fares near 13 Nov
        (DEP + window, 61.0),                                              # exactly 30 days: counts
        (DEP + window + timedelta(days=1), 30.0),                          # 31 days: ignored
        (date(2027, 1, 20), 25.0), (date(2027, 1, 22), 25.0),              # January: ignored
    )

    base = PriceIndex(history).baseline(_leg())

    assert base.samples == 6 and base.low == 60.0


def test_a_busy_key_still_needs_six_fares_near_this_departure():
    far = [(date(2027, 2, 1) + timedelta(days=i), 60.0) for i in range(10)]
    history = _around(*far, (DEP, 58.0), (DEP + timedelta(days=3), 62.0))

    assert PriceIndex(history).baseline(_leg()) is None  # 12 fares for the key, 2 near 13 Nov
    assert PriceIndex(history).baseline(_leg(dep=date(2027, 2, 5))).samples == 10


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


SIX_FARES = (58.0, 60.0, 62.0, 59.0, 61.0, 60.0)


def _six_fares(**extra):
    """Six different November weekend fares: 13–18 Nov departures."""
    return [_leg(price=p, dep=DEP + timedelta(days=i), **extra) for i, p in enumerate(SIX_FARES)]


async def test_drop_after_three_cycles_is_flagged(engine):
    await init_db()
    for _ in range(3):
        await build_trips(_six_fares(), [])

    [trip] = await build_trips([_leg(price=35.0)], [])

    assert trip.is_anomaly
    assert trip.normal_price_eur == 60.0
    assert trip.discount_pct == pytest.approx(41.7, abs=0.1)
    assert "below the usual €60" in trip.anomaly_description


async def _record_jfk_history(days_apart: int) -> None:
    for i, prices in enumerate(JFK_HISTORY):  # 21 different November fares
        await record_fares([_leg(price=p, dep=date(2026, 11, 1) + timedelta(days=i * 7 + j), **JFK)
                            for j, p in enumerate(prices)], f"c{i}")
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


async def test_the_same_fare_every_cycle_is_one_fare(engine):
    await init_db()
    for i in range(3):
        await record_fares([_leg(price=60.0 + j) for j in range(3)], f"c{i}")  # one itinerary

    index = await PriceIndex.load(exclude_cycle="now")

    assert index.baseline(_leg()) is None


async def test_the_same_fare_from_two_sources_is_one_fare(engine):
    await init_db()
    fares = _six_fares()[:5]
    google = _leg(price=59.5, source="google_flights")  # the same itinerary as Ryanair's first fare
    await record_fares(fares, "c1")
    await record_fares(fares + [google], "c2")

    index = await PriceIndex.load(exclude_cycle="now")

    assert index.baseline(_leg()) is None  # 5 fares, not 6


async def test_feed_deals_stay_out_of_the_history(engine):
    await init_db()
    recorded = await record_fares([_leg(is_feed_deal=True), _leg()], "c1")
    assert recorded == 1


async def test_rows_from_before_fares_were_dated_are_ignored(engine):
    await init_db()
    async with aiosqlite.connect(await get_db_path()) as db:
        for i in range(10):
            await db.execute(
                "INSERT INTO price_history (route, price_eur, source, recorded_at, origin_city, dest_city, "
                "trip_type, nights_bucket, depart_month, cycle_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("MXP-KRK", 60.0 + i, "old", datetime.utcnow().isoformat(), *astuple(KEY), "2026-11", f"c{i % 2}"),
            )
        await db.commit()

    index = await PriceIndex.load(exclude_cycle="now")

    assert index.baseline(_leg()) is None


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
        row = await (await db.execute(
            "SELECT origin_city, nights_bucket, cycle_id, depart_date, return_date FROM price_history")).fetchone()
    assert row == ("MIL", "weekend", "c1", "2026-11-13", "2026-11-16")


# ── Retention and sending order ───────────────────────────────────────────────

def _trip(hash_: str, discount: Optional[float], cost: float = 50.0) -> Trip:
    leg = _leg(price=cost)
    return Trip(
        hash=hash_, trip_id=hash_, deal_type=DealType.FLIGHT_ONLY, route="Milan → Krakow",
        outbound_flight=leg, flight_cost_eur=cost, total_cost_eur=cost, discount_pct=discount,
        alert_tier=AlertTier.INSTANT, data_confidence_score=0.9,
    )


async def test_summaries_judge_exactly_like_every_observation(engine):
    """B46: the database sums each fare's history up; every baseline and judgement stays the same."""
    rng = random.Random(46)
    await init_db()
    now = datetime.utcnow()
    routes = {("MXP", "KRK"): 3, ("MXP", "PRG"): 6, ("MXP", "BCN"): None}  # weekend, short, one-way
    rows = []
    for c in range(48):  # 48 searches over 12 days (and a few 35 days ago, outside the lookback)
        at = now - timedelta(hours=6 * c) - (timedelta(days=35) if c >= 45 else timedelta())
        stamp = (at if c % 5 else at.replace(microsecond=0)).isoformat()  # some without microseconds
        for (origin, dest), nights in routes.items():
            key = price_key(_leg(origin=origin, dest=dest, nights=nights))
            for _ in range(rng.randint(0, 10)):  # the same fare can come twice in one search
                depart = DEP + timedelta(days=rng.randint(-40, 40))
                ret = (depart + timedelta(days=nights)).isoformat() if nights else ""
                rows.append((f"{origin}-{dest}", round(rng.uniform(40, 200), 2), "ryanair", stamp, *astuple(key),
                             f"{depart:%Y-%m}", f"cycle{c}", depart.isoformat(), ret))
    rows.append(("MXP-KRK", 99.0, "ryanair", now.isoformat(), *astuple(KEY), "2026-11", "cycle3",
                 DEP.isoformat(), (DEP + timedelta(days=3)).isoformat()))  # a same-second tie, broken by id
    async with aiosqlite.connect(await get_db_path()) as db:
        await db.executemany(
            "INSERT INTO price_history (route, price_eur, source, recorded_at, origin_city, dest_city, trip_type,"
            " nights_bucket, depart_month, cycle_id, depart_date, return_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        await db.commit()
        # The reference: every observation, oldest first, as PriceIndex was fed before B46
        cursor = await db.execute(
            """
            SELECT origin_city, dest_city, trip_type, nights_bucket,
                   route || ' ' || depart_date || ' ' || COALESCE(return_date, ''),
                   price_eur, cycle_id, substr(recorded_at, 1, 10), depart_date
            FROM price_history
            WHERE recorded_at >= ? AND cycle_id IS NOT NULL AND cycle_id != ?
              AND origin_city IS NOT NULL AND depart_date IS NOT NULL
            ORDER BY recorded_at, id
            """, ((datetime.utcnow() - timedelta(days=30)).isoformat(), "cycle7"))
        reference = PriceIndex(Observation(PriceKey(*r[:4]), *r[4:8], date.fromisoformat(r[8]))
                               for r in await cursor.fetchall())

    summarised = await PriceIndex.load(exclude_cycle="cycle7")

    judged = {"baselines": 0, "anomalies": 0, "error fares": 0, "new lows": 0}
    for (origin, dest), nights in routes.items():
        for offset in range(-50, 51, 2):
            for price in (5.0, 30.0, 70.0, 120.0, 250.0):
                leg = _leg(price=price, origin=origin, dest=dest, nights=nights, dep=DEP + timedelta(days=offset))
                mine, theirs = summarised.baseline(leg), reference.baseline(leg)
                assert (mine is None) == (theirs is None), leg
                if mine:
                    assert (mine.median, mine.low, mine.samples, mine.is_established) == \
                           (theirs.median, theirs.low, theirs.samples, theirs.is_established)
                    judged["baselines"] += 1
                result = summarised.check(leg)
                assert result == reference.check(leg), leg
                judged["anomalies"] += result.is_anomaly
                judged["error fares"] += result.is_possible_error_fare
                judged["new lows"] += result.is_all_time_low
    assert all(judged.values()), judged  # the comparison covered every kind of judgement


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


async def test_history_is_kept_45_days_and_the_space_given_back(engine):
    await init_db()
    path = await get_db_path()
    now = datetime.utcnow()
    rows = [("MXP-KRK", 50.0 + i % 40, "x", (now - timedelta(days=50 if i % 4 else 40)).isoformat())
            for i in range(40_000)]
    async with aiosqlite.connect(path) as db:
        await db.executemany("INSERT INTO price_history (route, price_eur, source, recorded_at) VALUES (?, ?, ?, ?)",
                             rows)
        await db.commit()
    before = os.path.getsize(path)

    purged = await purge_old_rows(vacuum_min_free_mb=0)

    assert purged["price_history"] == 30_000  # 50 days old; the 10,000 from 40 days ago stay
    assert os.path.getsize(path) < before / 2  # VACUUM gave the space back
    assert await get_state("last_vacuum") == now.date().isoformat()
    async with aiosqlite.connect(path) as db:
        await db.executemany("INSERT INTO price_history (route, price_eur, source, recorded_at) VALUES (?, ?, ?, ?)",
                             rows)
        await db.commit()
    assert "vacuumed_mb" not in await purge_old_rows(vacuum_min_free_mb=0)  # once a day at most


async def test_init_db_drops_the_unused_route_index(engine):
    async with aiosqlite.connect(await get_db_path()) as db:
        await db.execute("CREATE TABLE price_history (id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL,"
                         " price_eur REAL NOT NULL, source TEXT NOT NULL, recorded_at TEXT NOT NULL)")
        await db.execute("CREATE INDEX idx_prices_route ON price_history(route)")
        await db.commit()

    await init_db()

    async with aiosqlite.connect(await get_db_path()) as db:
        indexes = {r[0] for r in await (await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'price_history'")).fetchall()}
    assert "idx_prices_route" not in indexes and "idx_prices_key" in indexes


async def test_pending_instants_go_out_best_discount_first(engine):
    await init_db()
    await save_deal(_trip("cheap-no-discount", None, cost=20.0))
    await save_deal(_trip("big-discount", 55.0, cost=45.0))
    await save_deal(_trip("small-discount", 36.0, cost=40.0))

    pending = await get_pending_instant_alerts(limit=10)

    assert [t.hash for t in pending] == ["big-discount", "small-discount", "cheap-no-discount"]
