"""Shared pytest fixtures."""
from __future__ import annotations

import os

import pytest


# Override DB path to use a temp file during tests
@pytest.fixture(autouse=True, scope="session")
def test_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("data")
    db_path = str(tmp / "test_travel_deals.db")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    return db_path
