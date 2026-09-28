"""
Scraper Health Monitor

Tracks success/failure rates per scraper source.
Reports which sources are working, degraded, or failing.
Thread-safe via asyncio lock.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config import get_settings
from utils.logging_config import get_logger

log = get_logger(__name__)

_STALE_MULTIPLIER = 3  # source is stale after 3× scrape interval with no success


@dataclass
class ScraperHealth:
    source_id: str
    last_run_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_result_count: int = 0
    consecutive_failures: int = 0
    total_runs: int = 0
    total_successes: int = 0

    @property
    def status(self) -> str:
        if self.total_runs == 0:
            return "UNKNOWN"
        stale_after = timedelta(
            minutes=get_settings().scrape_interval_minutes * _STALE_MULTIPLIER
        )
        is_stale = (
            self.last_success_at is None
            or (datetime.utcnow() - self.last_success_at) > stale_after
        )
        if is_stale and self.total_runs > 2:
            return "STALE"
        if self.consecutive_failures >= 3:
            return "FAILING"
        if self.consecutive_failures >= 1:
            return "DEGRADED"
        return "OK"

    @property
    def success_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.total_successes / self.total_runs

    def to_line(self) -> str:
        last = self.last_success_at.strftime("%H:%M") if self.last_success_at else "never"
        return (
            f"{self.source_id:28s}  {self.status:8s}  "
            f"ok={self.total_successes}/{self.total_runs}  "
            f"fail_streak={self.consecutive_failures}  "
            f"last_count={self.last_result_count}  "
            f"last_ok={last}"
        )


class ScraperHealthMonitor:
    """In-memory health tracker. Resets on restart (by design — health is a live signal)."""

    def __init__(self) -> None:
        self._registry: Dict[str, ScraperHealth] = {}
        self._lock = asyncio.Lock()

    async def record_result(
        self,
        source_id: str,
        count: int,
        success: bool,
    ) -> None:
        async with self._lock:
            if source_id not in self._registry:
                self._registry[source_id] = ScraperHealth(source_id=source_id)
            h = self._registry[source_id]
            h.last_run_at = datetime.utcnow()
            h.total_runs += 1
            h.last_result_count = count
            if success and count >= 0:
                h.last_success_at = datetime.utcnow()
                h.consecutive_failures = 0
                h.total_successes += 1
            else:
                h.consecutive_failures += 1
                if h.consecutive_failures == 3:
                    log.warning(
                        "scraper_degraded",
                        source=source_id,
                        consecutive_failures=h.consecutive_failures,
                    )

    async def get_health_report(self) -> List[ScraperHealth]:
        async with self._lock:
            return list(self._registry.values())

    async def get_summary(self) -> str:
        report = await self.get_health_report()
        if not report:
            return "No scraper health data (run a cycle first)"
        lines = ["Scraper Health Report", "─" * 70]
        for h in sorted(report, key=lambda x: x.source_id):
            lines.append(h.to_line())
        ok = sum(1 for h in report if h.status == "OK")
        total = len(report)
        lines.append(f"\n{ok}/{total} sources healthy")
        return "\n".join(lines)


# Module-level singleton
_monitor: Optional[ScraperHealthMonitor] = None


def get_health_monitor() -> ScraperHealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ScraperHealthMonitor()
    return _monitor
