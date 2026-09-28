"""Time helpers."""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """
    Current UTC time as a naive datetime.

    Replaces the deprecated datetime.utcnow(). Stays naive on purpose: the
    database stores naive ISO strings and every comparison in the code base
    is between naive UTC datetimes.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
