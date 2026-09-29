"""
The deal engine as a service (B37).

`Engine` owns what runs 24/7: the scheduler (a search every
SCRAPE_INTERVAL_MINUTES and the daily digest), the notifier and the Telegram
command bot. On top of that it gives the desktop app its controls and a status
snapshot:

  search_now()      run a cycle now (never two at once: cycles share a lock)
  pause() / resume() hold or release alerts (the same state as Telegram /pause)
  status()          state, next and last search, searches today

Headless use (`main.py run`, Docker) is `await Engine().run()`, which stops on
SIGTERM or SIGINT. What happens is announced on the event bus (utils/events.py).
"""
from __future__ import annotations

import asyncio
import html
import signal
from datetime import datetime, timezone
from typing import Any, Coroutine, Dict, Optional, Set

import control
from config import POPULAR_DESTINATIONS, get_settings
from notifier.commands import CommandBot
from notifier.telegram import TelegramNotifier
from preferences import get_preferences
from scheduler import runner
from storage.database import close_interrupted_cycles, count_cycles_since, init_db, recent_cycles
from utils.events import get_event_bus
from utils.logging_config import get_logger
from utils.timeutil import utcnow

log = get_logger(__name__)

STATES = ("stopped", "searching", "paused", "attention", "running")


class Engine:
    def __init__(
        self,
        notifier: Any = None,
        aggregator: Any = None,
        *,
        telegram_commands: bool = True,
    ) -> None:
        self._notifier = notifier
        self._aggregator = aggregator
        self._telegram_commands = telegram_commands
        self._scheduler = None
        self._cycle_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._tasks: Set[asyncio.Task] = set()
        self._running = False
        self._bus = get_event_bus()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @property
    def notifier(self) -> Any:
        return self._notifier

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_searching(self) -> bool:
        return self._cycle_lock.locked()

    async def start(self, first_search: bool = True) -> None:
        """Start the scheduler and the command bot; by default search right away."""
        await init_db()
        await close_interrupted_cycles()
        if self._notifier is None:
            self._notifier = await TelegramNotifier.create()
        self._scheduler = runner.create_scheduler(self._scheduled_cycle, self._scheduled_digest)
        self._scheduler.start()
        self._running = True
        prefs = get_preferences()
        log.info(
            "scheduler_started",
            interval_min=get_settings().scrape_interval_minutes,
            home_airports=prefs.home_airports,
            trip_lengths=prefs.preferred_trip_lengths,
            search_window_days=prefs.search_window_days,
        )
        await self._notifier.send_system_message(
            "🚀 <b>Travel Deal Intelligence Engine started</b>\n"
            f"Origins: {html.escape(', '.join(prefs.home_airports))}\n"
            f"Profiles: {html.escape(', '.join(prefs.preferred_trip_lengths))}\n"
            f"Monitoring {len(POPULAR_DESTINATIONS)} destinations"
        )
        if self._telegram_commands:
            # Telegram commands (/deals, /mute, /pause, ...) are polled alongside the scheduler
            self._start_command_bot()
        self._bus.publish("engine_started")
        if first_search:
            await self.search_now(trigger="startup")

    async def stop(self) -> None:
        """Stop the scheduler, the command bot and a running search (cancelled), then return."""
        if not self._running:
            return
        self._running = False
        self._stop.set()
        log.info("scheduler_stopping")
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._bus.publish("engine_stopped")
        log.info("engine_stopped")

    async def run(self) -> None:
        """Headless: start, run until SIGTERM/SIGINT (docker stop, Ctrl-C) or stop(), then stop cleanly."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):  # e.g. Windows: Ctrl-C raises instead
                pass
        try:
            await self.start()
            await self._stop.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError):
                    pass

    def _spawn(self, coro: Coroutine) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ── Searches ──────────────────────────────────────────────────────────────

    async def run_cycle(self, trigger: str = "manual") -> Optional[Dict[str, Any]]:
        """Run one search cycle now and wait for it; None when one is already running."""
        if self._cycle_lock.locked():
            log.info("cycle_skipped_already_running", trigger=trigger)
            return None
        async with self._cycle_lock:
            return await runner.run_pipeline_cycle(self._aggregator, self._notifier, trigger=trigger)

    async def search_now(self, trigger: str = "manual") -> bool:
        """Start a search in the background; False when stopped or one is already running."""
        if not self._running or self._cycle_lock.locked():
            return False
        self._spawn(self.run_cycle(trigger))
        await asyncio.sleep(0)  # let it take the lock, so a second click sees it running
        return True

    async def _scheduled_cycle(self) -> None:
        await self.run_cycle("scheduled")

    async def _scheduled_digest(self) -> None:
        await runner.run_daily_digest(self._notifier)

    # ── Alerts ────────────────────────────────────────────────────────────────

    async def pause(self, hours: Optional[float] = None, until: Optional[datetime] = None) -> datetime:
        return await control.pause(hours, until)

    async def resume(self) -> None:
        await control.resume()

    # ── Settings changes (B38: the app's Settings screen) ─────────────────────

    @property
    def sources(self) -> list:
        """The scrapers the next search uses (their enabled state and reason)."""
        return (self._aggregator or runner.get_aggregator()).sources

    async def apply_settings_change(
        self, *, reschedule: bool = False, reload_notifier: bool = False, reload_sources: bool = False,
    ) -> None:
        """Pick up saved settings without a restart."""
        settings = get_settings()
        if reschedule and self._scheduler is not None and self._running:
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger

            self._scheduler.reschedule_job(
                "pipeline_cycle", trigger=IntervalTrigger(minutes=settings.scrape_interval_minutes))
            self._scheduler.reschedule_job("daily_digest", trigger=CronTrigger(
                hour=settings.digest_hour, minute=settings.digest_minute, timezone=settings.timezone))
        if reload_notifier and isinstance(self._notifier, TelegramNotifier):
            self._notifier = await TelegramNotifier.create()
            if self._telegram_commands and self._running:
                for task in [t for t in self._tasks if getattr(t, "is_command_bot", False)]:
                    task.cancel()
                self._start_command_bot()
        if reload_sources and self._aggregator is None:
            runner.reset_aggregator()
        log.info("settings_applied", reschedule=reschedule, reload_notifier=reload_notifier,
                 reload_sources=reload_sources)

    def _start_command_bot(self) -> None:
        task = self._spawn(CommandBot(self._notifier).run(self._stop))
        task.is_command_bot = True  # type: ignore[attr-defined]

    # ── Status ────────────────────────────────────────────────────────────────

    def next_search_at(self) -> Optional[datetime]:
        """When the next scheduled search runs (naive UTC), if the engine is running."""
        job = self._scheduler.get_job("pipeline_cycle") if self._scheduler is not None and self._running else None
        if job is None or job.next_run_time is None:
            return None
        return job.next_run_time.astimezone(timezone.utc).replace(tzinfo=None)

    async def status(self) -> Dict[str, Any]:
        """A snapshot for the app's status pill and Activity screen."""
        paused = await control.paused_until()
        finished = [c for c in await recent_cycles(limit=5) if c["status"] != "running"]
        last = finished[0] if finished else None
        midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if not self._running:
            state = "stopped"
        elif self.is_searching:
            state = "searching"
        elif paused:
            state = "paused"
        elif last and last["status"] == "failed":
            state = "attention"
        else:
            state = "running"
        next_at = self.next_search_at()
        return {
            "state": state,
            "searching": self.is_searching,
            "paused_until": paused.isoformat() if paused else None,
            "next_search_at": next_at.isoformat() if next_at else None,
            "last_cycle": last,
            "searches_today": await count_cycles_since(midnight),
        }
