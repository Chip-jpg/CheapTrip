"""The shared database connection (B46): one per event loop, safe to share, never blocking an exit."""
from __future__ import annotations

import asyncio

import aiosqlite
import pytest

import storage.database as database
from storage.database import close_connections, connection, get_state, init_db, set_state
from tests.harness import flight


async def test_a_search_opens_a_few_connections_not_hundreds(engine, monkeypatch):
    await init_db()
    await close_connections()
    opened = []
    connect = aiosqlite.connect

    def counting(*args, **kwargs):
        opened.append(args)
        return connect(*args, **kwargs)

    monkeypatch.setattr(aiosqlite, "connect", counting)

    await engine.run_cycle(flights=[flight(destination=code, price=30.0) for code in ("KRK", "PRG", "BCN", "BUD")])

    assert 1 <= len(opened) <= 3  # the shared one, and the price history summary's own


async def test_blocks_take_turns_and_leave_nothing_uncommitted(engine):
    await init_db()
    order = []

    async def unfinished():
        async with connection() as db:
            order.append("first in")
            await db.execute("INSERT INTO engine_state (key, value, updated_at) VALUES ('left', 'x', 'now')")
            await asyncio.sleep(0.05)  # the other block must wait, not commit this row for it
            order.append("first out")

    async def committed():
        await asyncio.sleep(0.01)
        async with connection() as db:
            order.append("second in")
            await db.execute("INSERT INTO engine_state (key, value, updated_at) VALUES ('kept', 'y', 'now')")
            await db.commit()

    await asyncio.gather(unfinished(), committed())

    assert order == ["first in", "first out", "second in"]
    assert await get_state("left") is None  # rolled back when its block ended, as a closed connection would
    assert await get_state("kept") == "y"


async def test_an_error_inside_a_block_rolls_its_writes_back(engine):
    await init_db()
    with pytest.raises(ValueError):
        async with connection() as db:
            await db.execute("INSERT INTO engine_state (key, value, updated_at) VALUES ('oops', 'x', 'now')")
            raise ValueError("boom")
    assert await get_state("oops") is None
    await set_state("fine", "1")  # and the connection still works
    assert await get_state("fine") == "1"


async def test_a_block_inside_a_block_is_refused_not_deadlocked(engine):
    await init_db()
    async with connection():
        with pytest.raises(RuntimeError, match="inside another"):
            async with connection():
                pass


async def test_row_format_changes_stay_inside_their_block(engine):
    await init_db()
    async with connection() as db:
        db.row_factory = aiosqlite.Row
    async with connection() as db:
        assert db.row_factory is None


async def test_the_connection_never_keeps_the_app_from_exiting(engine):
    await init_db()
    async with connection() as shared:
        mode = (await (await shared.execute("PRAGMA journal_mode")).fetchone())[0]
        assert shared._thread.daemon  # guards an aiosqlite upgrade changing how it runs
    async with connection(own=True) as own:
        assert own._thread.daemon and own is not shared
    assert mode == "wal"
    await close_connections()
    assert database._shared is None
    await set_state("again", "1")  # the next use opens it again
    assert await get_state("again") == "1"


async def test_tasks_asking_at_once_share_one_new_connection(engine, monkeypatch):
    await init_db()
    await close_connections()
    opened = []
    connect = aiosqlite.connect
    monkeypatch.setattr(aiosqlite, "connect", lambda *a, **k: opened.append(a) or connect(*a, **k))

    await asyncio.gather(*(get_state(f"k{i}") for i in range(10)))

    assert len(opened) == 1


async def test_closing_waits_for_the_query_in_progress(engine):
    await init_db()
    finished = []

    async def slow():
        async with connection() as db:
            await asyncio.sleep(0.05)
            await db.execute("INSERT INTO engine_state (key, value, updated_at) VALUES ('slow', 'x', 'now')")
            await db.commit()
            finished.append("query")

    task = asyncio.ensure_future(slow())
    await asyncio.sleep(0.01)
    await close_connections()
    finished.append("closed")
    await task

    assert finished == ["query", "closed"]
    assert await get_state("slow") == "x"
