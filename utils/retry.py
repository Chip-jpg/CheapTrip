from __future__ import annotations

import asyncio
import functools
import logging as _logging
from typing import Any, Callable, Optional, Tuple, Type

from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from utils.logging_config import get_logger

log = get_logger(__name__)


def async_retry(
    max_attempts: int = 4,
    min_wait: float = 2.0,
    max_wait: float = 30.0,
    retry_on: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """Decorator: async retry with exponential backoff."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_attempts),
                wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
                retry=retry_if_exception_type(retry_on),
                before_sleep=before_sleep_log(log, _logging.WARNING),
                reraise=False,
            ):
                with attempt:
                    return await func(*args, **kwargs)
            return None  # all attempts exhausted

        return wrapper

    return decorator


async def retry_call(
    coro_fn: Callable,
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> Optional[Any]:
    """Imperative retry helper for one-off calls."""
    delay = base_delay
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                log.warning(
                    "retry_attempt",
                    fn=coro_fn.__name__,
                    attempt=attempt,
                    wait=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
                delay *= 2
    log.error(
        "retry_exhausted",
        fn=coro_fn.__name__,
        attempts=max_attempts,
        error=str(last_exc),
    )
    return None
