"""
Scraper Health Monitor

Tracks per-source outcomes in the scraper_health table, so health survives
restarts and `python main.py health` works from a separate process.

Status per source:
  OK        last runs succeeded and returned data
  EMPTY     runs succeed but have returned nothing for a while
  DEGRADED  1–2 consecutive failures
  FAILING   3+ consecutive failures (errors, rate limits, blocks)
  STALE     no successful run for 3× the scrape interval
  DISABLED  turned off in settings / missing credentials
  UNKNOWN   never ran
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import aiosqlite

from config import get_settings
from scrapers.base import ScrapeOutcome, ScrapeStatus
from storage.database import get_db_path
from utils.logging_config import get_logger

log = get_logger(__name__)

_STALE_MULTIPLIER = 3   # stale after 3× scrape interval with no success
_FAILING_STREAK = 3
_EMPTY_STREAK = 10      # this many empty runs in a row → EMPTY

_FAILURE_STATUSES = {ScrapeStatus.ERROR, ScrapeStatus.RATE_LIMITED, ScrapeStatus.BLOCKED}

_COLUMNS = (
    "source_id", "last_run_at", "last_success_at", "last_status", "last_count", "last_error",
    "consecutive_failures", "consecutive_empty", "total_runs", "total_successes", "notified_status",
)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


@dataclass
class ScraperHealth:
    source_id: str
    last_run_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_count: int = 0
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    consecutive_empty: int = 0
    total_runs: int = 0
    total_successes: int = 0
    notified_status: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "ScraperHealth":
        data = dict(zip(_COLUMNS, row))
        data["last_run_at"] = _parse_dt(data["last_run_at"])
        data["last_success_at"] = _parse_dt(data["last_success_at"])
        return cls(**data)

    @property
    def status(self) -> str:
        if self.last_status == ScrapeStatus.DISABLED.value:
            return "DISABLED"
        if self.total_runs == 0:
            return "UNKNOWN"
        if self.consecutive_failures >= _FAILING_STREAK:
            return "FAILING"
        if self.consecutive_failures >= 1:
            return "DEGRADED"
        stale_after = timedelta(minutes=get_settings().scrape_interval_minutes * _STALE_MULTIPLIER)
        is_stale = self.last_success_at is None or (datetime.utcnow() - self.last_success_at) > stale_after
        if is_stale and self.total_runs > 2:
            return "STALE"
        if self.consecutive_empty >= _EMPTY_STREAK:
            return "EMPTY"
        return "OK"

    @property
    def success_rate(self) -> float:
        return self.total_successes / self.total_runs if self.total_runs else 0.0

    def to_line(self) -> str:
        last = self.last_success_at.strftime("%m-%d %H:%M") if self.last_success_at else "never"
        line = (
            f"{self.source_id:22s}  {self.status:8s}  "
            f"ok={self.total_successes}/{self.total_runs}  "
            f"fail_streak={self.consecutive_failures}  "
            f"last={self.last_status or '-'}:{self.last_count}  "
            f"last_ok={last}"
        )
        if self.last_error and self.status == "DISABLED":
            line += f"\n{'':24s}note: {self.last_error[:120]}"
        elif self.last_error and self.status != "OK":
            line += f"\n{'':24s}error: {self.last_error[:120]}"
        return line


class ScraperHealthMonitor:
    """DB-backed health tracker."""

    async def _load(self, db: aiosqlite.Connection, source_id: str) -> ScraperHealth:
        cursor = await db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM scraper_health WHERE source_id = ?", (source_id,)
        )
        row = await cursor.fetchone()
        return ScraperHealth.from_row(row) if row else ScraperHealth(source_id=source_id)

    async def record(self, outcome: ScrapeOutcome) -> ScraperHealth:
        now = datetime.utcnow()
        async with aiosqlite.connect(await get_db_path()) as db:
            h = await self._load(db, outcome.source_id)
            h.last_status = outcome.status.value
            if outcome.status == ScrapeStatus.DISABLED:
                h.last_error = outcome.error  # why it is off (e.g. a cooldown), or None
            else:
                h.last_run_at = now
                h.total_runs += 1
                h.last_count = outcome.count
                h.last_error = outcome.error
                if outcome.status in _FAILURE_STATUSES:
                    h.consecutive_failures += 1
                    if h.consecutive_failures == _FAILING_STREAK:
                        log.warning(
                            "scraper_failing",
                            source=outcome.source_id,
                            status=outcome.status.value,
                            error=outcome.error,
                        )
                else:
                    h.consecutive_failures = 0
                    h.total_successes += 1
                    h.last_success_at = now
                    h.consecutive_empty = h.consecutive_empty + 1 if outcome.count == 0 else 0
            await self._save(db, h)
            await db.commit()
        return h

    async def _save(self, db: aiosqlite.Connection, h: ScraperHealth) -> None:
        values = (
            h.source_id,
            h.last_run_at.isoformat() if h.last_run_at else None,
            h.last_success_at.isoformat() if h.last_success_at else None,
            h.last_status, h.last_count, h.last_error,
            h.consecutive_failures, h.consecutive_empty, h.total_runs, h.total_successes,
            h.notified_status,
        )
        placeholders = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS[1:])
        await db.execute(
            f"INSERT INTO scraper_health ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(source_id) DO UPDATE SET {updates}",
            values,
        )

    async def get_health_report(self) -> List[ScraperHealth]:
        async with aiosqlite.connect(await get_db_path()) as db:
            cursor = await db.execute(f"SELECT {', '.join(_COLUMNS)} FROM scraper_health ORDER BY source_id")
            rows = await cursor.fetchall()
        return [ScraperHealth.from_row(r) for r in rows]

    async def get_summary(self) -> str:
        report = await self.get_health_report()
        if not report:
            return "No scraper health data (run a cycle first)"
        lines = ["Scraper Health Report", "─" * 70]
        lines.extend(h.to_line() for h in report)
        active = [h for h in report if h.status != "DISABLED"]
        ok = sum(1 for h in active if h.status == "OK")
        disabled = len(report) - len(active)
        lines.append(f"\n{ok}/{len(active)} active sources healthy ({disabled} disabled)")
        return "\n".join(lines)

    async def pending_notifications(self) -> List[Tuple[ScraperHealth, str]]:
        """Sources that became FAILING, or recovered, since the last notice: (health, 'failing'|'recovered')."""
        changes = []
        for h in await self.get_health_report():
            if h.status == "FAILING" and h.notified_status != "FAILING":
                changes.append((h, "failing"))
            elif h.status == "OK" and h.notified_status == "FAILING":
                changes.append((h, "recovered"))
        return changes

    async def mark_notified(self, source_id: str, status: str) -> None:
        async with aiosqlite.connect(await get_db_path()) as db:
            await db.execute(
                "UPDATE scraper_health SET notified_status = ? WHERE source_id = ?", (status, source_id)
            )
            await db.commit()


# Module-level singleton
_monitor: Optional[ScraperHealthMonitor] = None


def get_health_monitor() -> ScraperHealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ScraperHealthMonitor()
    return _monitor
