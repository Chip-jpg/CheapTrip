"""
A small in-process event bus (B37).

The engine publishes what happens (a cycle starts, a source finishes, alerts
are paused, ...) and the desktop app's screens (server-sent events) and tray
icon listen. Publishing never blocks and never fails the publisher: a slow
subscriber just misses events once its queue is full.

Events are dicts: {"type": "source_done", "at": "<UTC ISO time>", ...data}.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Set

from utils.logging_config import get_logger
from utils.timeutil import utcnow

log = get_logger(__name__)

Event = Dict[str, Any]
QUEUE_SIZE = 200


class EventBus:
    def __init__(self) -> None:
        self._queues: Set[asyncio.Queue] = set()
        self._listeners: List[Callable[[Event], None]] = []

    def publish(self, kind: str, **data: Any) -> Event:
        event: Event = {"type": kind, "at": utcnow().isoformat(), **data}
        for queue in list(self._queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.debug("event_dropped_slow_subscriber", type=kind)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as exc:  # a listener must never break the engine
                log.warning("event_listener_failed", type=kind, error=str(exc))
        return event

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue]:
        """A queue receiving every event published while the block is open (async consumers)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues.add(queue)
        try:
            yield queue
        finally:
            self._queues.discard(queue)

    def add_listener(self, listener: Callable[[Event], None]) -> Callable[[], None]:
        """Call `listener(event)` for every event, in the publisher's thread; returns an unsubscribe."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None


_bus = EventBus()


def get_event_bus() -> EventBus:
    return _bus
