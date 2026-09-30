"""
The engine and the local API in a background thread (B40).

The app window must own the main thread (Windows GUI), so the engine runs on
its own asyncio loop here. The tray and the window reach it through submit(),
and get the status whenever something happens (and every half minute, so the
tray's "next search" and a pause that ends stay current).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Callable, Coroutine, Dict, Optional

from api.server import ApiServer, full_status, ui_dir
from storage.database import close_connections, init_db
from utils.events import get_event_bus
from utils.logging_config import get_logger

log = get_logger(__name__)

STATUS_REFRESH_S = 30.0


class EngineThread:
    def __init__(
        self,
        engine_factory: Callable[[], Any],
        *,
        shell: Any = None,
        on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
        port: int = 0,
    ) -> None:
        self._engine_factory = engine_factory
        self._shell = shell
        self._on_status = on_status
        self._port = port
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopping: Optional[asyncio.Event] = None
        self.engine: Any = None
        self.server: Optional[ApiServer] = None

    def start(self, timeout: float = 60.0) -> ApiServer:
        """Start the thread; returns once the API answers (the engine then starts searching)."""
        self._thread = threading.Thread(target=self._run, name="cheaptrip-engine", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("the engine didn't start in time")
        if self._error is not None:
            raise RuntimeError(f"the engine didn't start: {self._error}") from self._error
        return self.server

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except BaseException as exc:  # reported by start(), or logged if it happens later
            if not self._ready.is_set():
                self._error = exc
                self._ready.set()
            else:
                log.error("engine_thread_failed", error=str(exc), exc_info=True)

    async def _main(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._stopping = asyncio.Event()
        await init_db()  # the screens load at once: a new install's tables must exist first
        self.engine = self._engine_factory()
        self.server = ApiServer(self.engine, static_dir=ui_dir(), shell=self._shell)
        await self.server.start(self._port)
        self._ready.set()
        watcher = asyncio.create_task(self._watch_status())
        try:
            try:
                await self.engine.start()
            except Exception as exc:  # the window still opens and shows the engine stopped
                log.error("engine_start_failed", error=str(exc), exc_info=True)
            await self._stopping.wait()
        finally:
            watcher.cancel()
            await self.server.stop()
            await self.engine.stop()
            await close_connections()  # also when the engine never started

    async def _watch_status(self) -> None:
        if self._on_status is None:
            return
        with get_event_bus().subscribe() as queue:
            while True:
                try:
                    self._on_status(await full_status(self.engine))
                except Exception as exc:
                    log.warning("status_update_failed", error=str(exc))
                try:
                    await asyncio.wait_for(queue.get(), timeout=STATUS_REFRESH_S)
                    await asyncio.sleep(0.2)  # one update for a burst of events
                    while not queue.empty():
                        queue.get_nowait()
                except asyncio.TimeoutError:
                    pass

    def submit(self, coro: Coroutine) -> concurrent.futures.Future:
        """Run a coroutine on the engine's loop, from any thread."""
        if self.loop is None:
            coro.close()
            raise RuntimeError("the engine isn't running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, coro: Coroutine, timeout: float = 30.0) -> Any:
        return self.submit(coro).result(timeout)

    def stop(self, timeout: float = 30.0) -> None:
        """Stop the engine (a running search is cancelled) and the API, and wait for the thread."""
        if self.loop is not None and self._stopping is not None and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self._stopping.set)
            except RuntimeError:  # the loop already closed
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)
