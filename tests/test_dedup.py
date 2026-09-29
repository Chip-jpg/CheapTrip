"""Fuzzy deduplication (B17): similar trips collapse; only a real price drop is a new deal."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import aiosqlite

from filters.deduplication import collapse_similar, deduplicate_trips, fingerprint
from storage.database import get_db_path, init_db
from storage.models import AlertTier, BookingConfidence, DealType, FlightLeg, HotelDeal, Trip
from tests.harness import flight

DEP = date(2026, 11, 13)


def _trip(price: float = 40.0, origin: str = "MXP", dest: str = "KRK", dep: Optional[date] = DEP,
          nights: Optional[int] = 3, source: str = "ryanair", tier: AlertTier = AlertTier.DIGEST) -> Trip:
    ret = dep + timedelta(days=nights) if dep and nights else None
    leg = FlightLeg(origin=origin, destination=dest, price_eur=price, departure_date=dep, return_date=ret,
                    source=source, booking_url="https://example.com/book", data_confidence_score=0.8)
    trip = Trip(
        deal_type=DealType.FLIGHT_ONLY, route="Milan → Krakow", outbound_flight=leg,
        flight_cost_eur=price, total_cost_eur=price, departure_date=dep, return_date=ret, nights=nights,
        source_list=[source], alert_tier=tier, data_confidence_score=0.8,
        booking_confidence=BookingConfidence.MEDIUM,
    )
    trip.hash = trip.compute_hash()
    return trip


# ── Fingerprints ──────────────────────────────────────────────────────────────

def test_fingerprint_ignores_price_dates_and_milan_airport():
    assert fingerprint(_trip(40.0)) == fingerprint(_trip(55.0, origin="BGY", dep=DEP + timedelta(days=1)))
    assert fingerprint(_trip()) == ("MIL", "KRK", "flight", "weekend")
    # 2 and 4 nights are both a weekend
    assert fingerprint(_trip(nights=2)) == fingerprint(_trip(nights=4))


def test_fingerprint_separates_length_profiles_and_hotels():
    assert fingerprint(_trip(nights=3)) != fingerprint(_trip(nights=6))
    assert fingerprint(_trip(nights=None))[3] == ""
    complete = _trip()
    complete.deal_type = DealType.COMPLETE_TRIP
    complete.hotel = HotelDeal(name="Hotel Wawel", location="Krakow", price_per_night_eur=30, nights=3,
                               total_price_eur=90)
    assert fingerprint(complete)[2] == "stay:hotel wawel"


# ── Within a cycle ────────────────────────────────────────────────────────────

def test_departures_two_days_apart_collapse_into_the_cheapest():
    trips = [_trip(45.0, dep=DEP), _trip(39.0, dep=DEP + timedelta(days=2)), _trip(41.0, origin="BGY", dep=DEP)]

    kept = collapse_similar(trips)

    assert [t.total_cost_eur for t in kept] == [39.0]


def test_departures_further_apart_or_other_lengths_stay_separate():
    trips = [_trip(40.0, dep=DEP), _trip(42.0, dep=DEP + timedelta(days=3)), _trip(38.0, nights=6)]

    assert len(collapse_similar(trips)) == 3


def test_same_itinerary_from_two_sources_is_one_trip_with_both():
    ryanair = _trip(40.0, source="ryanair")
    google = _trip(44.0, source="google_flights")

    [kept] = collapse_similar([google, ryanair])

    assert kept.total_cost_eur == 40.0
    assert kept.source_list == ["ryanair", "google_flights"]
    assert kept.booking_confidence == BookingConfidence.HIGH  # 2 sources + a booking link


# ── Across cycles ─────────────────────────────────────────────────────────────

async def test_one_euro_cheaper_next_cycle_is_a_duplicate(engine):
    await init_db()
    new, _ = await deduplicate_trips([_trip(40.0)])
    assert len(new) == 1

    new, dupes = await deduplicate_trips([_trip(39.0, dep=DEP + timedelta(days=1))])

    assert new == [] and len(dupes) == 1


async def test_real_price_drop_is_a_new_deal_and_retires_the_old_copy(engine):
    await init_db()
    await deduplicate_trips([_trip(40.0, tier=AlertTier.INSTANT)])

    [new], _ = await deduplicate_trips([_trip(37.0)])  # 7.5% cheaper

    assert "price dropped from €40" in new.alert_reasons
    async with aiosqlite.connect(await get_db_path()) as db:
        tiers = dict(await (await db.execute("SELECT total_cost, alert_tier FROM deals")).fetchall())
    assert tiers == {40.0: "archive", 37.0: "digest"}


async def test_deal_older_than_the_window_does_not_block(engine):
    await init_db()
    await deduplicate_trips([_trip(40.0)])
    old = (datetime.utcnow() - timedelta(days=8)).isoformat()
    async with aiosqlite.connect(await get_db_path()) as db:
        await db.execute("UPDATE deals SET created_at = ?", (old,))
        await db.commit()

    new, _ = await deduplicate_trips([_trip(39.5, dep=DEP + timedelta(days=1))])

    assert len(new) == 1


async def test_repeated_feed_post_without_dates_is_a_duplicate(engine):
    await init_db()
    await deduplicate_trips([_trip(19.0, dep=None, nights=None)])

    new, _ = await deduplicate_trips([_trip(18.5, dep=None, nights=None, source="other_feed")])

    assert new == []


# ── Whole pipeline ────────────────────────────────────────────────────────────

async def test_small_price_moves_do_not_realert_but_a_real_drop_does(engine):
    await engine.run_cycle(flights=[flight(destination="KRK", price=25.0)])
    await engine.run_cycle(flights=[flight(destination="KRK", price=24.0)])  # 4% cheaper
    assert len(engine.telegram.texts) == 1

    await engine.run_cycle(flights=[flight(destination="KRK", price=21.0)])  # 16% cheaper

    assert len(engine.telegram.texts) == 2
    assert "price dropped from €25" in engine.telegram.texts[1]
    assert "<s>€25</s>" in engine.telegram.texts[1]


# ── First time a known fare qualifies for an instant alert (B27) ──────────────

async def test_saved_digest_fare_that_becomes_instant_is_offered_once(engine):
    await init_db()
    await deduplicate_trips([_trip(40.0, tier=AlertTier.DIGEST)])

    [promoted], _ = await deduplicate_trips([_trip(40.0, tier=AlertTier.INSTANT)])

    assert "now unusually cheap for this route" in promoted.alert_reasons
    async with aiosqlite.connect(await get_db_path()) as db:
        rows = await (await db.execute("SELECT alert_tier, created_at FROM deals")).fetchall()
    assert len(rows) == 1 and rows[0][0] == "instant"
    assert datetime.fromisoformat(rows[0][1]) > datetime.utcnow() - timedelta(minutes=1)

    # Once it has been instant, the same fare is a duplicate again
    new, dupes = await deduplicate_trips([_trip(40.0, tier=AlertTier.INSTANT)])
    assert new == [] and len(dupes) == 1


async def test_similar_fare_becoming_instant_retires_the_digest_copy(engine):
    await init_db()
    await deduplicate_trips([_trip(40.0, tier=AlertTier.DIGEST)])

    [new], _ = await deduplicate_trips([_trip(39.5, dep=DEP + timedelta(days=1), tier=AlertTier.INSTANT)])

    assert "now unusually cheap for this route" in new.alert_reasons
    async with aiosqlite.connect(await get_db_path()) as db:
        tiers = dict(await (await db.execute("SELECT total_cost, alert_tier FROM deals")).fetchall())
    assert tiers == {40.0: "archive", 39.5: "instant"}


async def test_promoted_fare_is_queued_by_its_new_discount(engine):
    # Round 4 dry run: promoted deals kept an empty discount, so the queue ranked them last
    from storage.database import get_pending_instant_alerts

    await init_db()
    await deduplicate_trips([_trip(40.0, tier=AlertTier.DIGEST)])  # cold start: no discount yet
    other = _trip(30.0, dest="PRG", tier=AlertTier.INSTANT)
    other.discount_pct = 36.0
    await deduplicate_trips([other])

    promoted = _trip(40.0, tier=AlertTier.INSTANT)
    promoted.discount_pct = 45.0
    await deduplicate_trips([promoted])

    assert [t.discount_pct for t in await get_pending_instant_alerts(limit=1)] == [45.0]
    async with aiosqlite.connect(await get_db_path()) as db:
        row = await (await db.execute("SELECT discount_pct, confidence FROM deals WHERE total_cost = 40.0")).fetchone()
    assert row == (45.0, 0.8)


async def test_already_sent_fare_is_not_offered_again(engine):
    from storage.database import mark_alerted

    await init_db()
    [sent], _ = await deduplicate_trips([_trip(40.0, tier=AlertTier.INSTANT)])
    await mark_alerted(sent.hash, AlertTier.INSTANT)

    new, _ = await deduplicate_trips([_trip(40.0, tier=AlertTier.INSTANT)])

    assert new == []


async def test_cold_start_fare_alerts_once_history_shows_it_is_cheap(engine):
    # Round 2 dry run: 7 fares qualified in cycle 3 but none alerted (saved as digest on cycle 1)
    from tests.test_pipeline import _same_month_days_ahead

    days = _same_month_days_ahead(6)
    prices = (35.0, 58.0, 60.0, 62.0, 59.0, 61.0)
    fares = [flight(destination="KRK", price=p, days_ahead=d) for p, d in zip(prices, days)]
    for _ in range(2):
        await engine.run_cycle(flights=fares)
    assert not any("FLIGHT DEAL" in t for t in engine.telegram.texts)

    await engine.run_cycle(flights=fares)  # six fares from two earlier cycles: €35 is ~41% below the usual €60
    await engine.run_cycle(flights=fares)

    alerts = [t for t in engine.telegram.texts if "FLIGHT DEAL" in t]
    assert len(alerts) == 1
    assert "€35" in alerts[0] and "now unusually cheap for this route" in alerts[0]
