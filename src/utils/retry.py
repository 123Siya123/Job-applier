"""Retry decorator with exponential backoff and jitter.

Tenacity-based wrapper that defaults to sane values for network/API calls.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .exceptions import JobApplierError
from .logger import get_logger

_log = get_logger("retry")
T = TypeVar("T")


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 4,
    initial_wait: float = 2.0,
    max_wait: float = 30.0,
    retry_on: type[BaseException] | tuple[type[BaseException], ...] = Exception,
    label: str = "operation",
) -> T:
    """Run `fn` with exponential backoff. Re-raises the last error if all
    attempts fail.

    Backoff schedule with defaults: 2s → 4s → 8s → 16s (jittered).
    """
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=initial_wait, max=max_wait),
            retry=retry_if_exception_type(retry_on),
            reraise=True,
        ):
            with attempt:
                return await fn()
    except RetryError as exc:  # pragma: no cover — tenacity reraise=True so this rarely fires
        raise JobApplierError(f"{label} exhausted retries") from exc
    raise JobApplierError(f"{label} unreachable code")  # pragma: no cover
