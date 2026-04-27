"""Simple token-bucket rate limiter.

We use one global limiter for outbound HTTP / Gemini calls so the system
can never accidentally hammer a single host or burn through API quota.
"""

from __future__ import annotations

import asyncio
import time


class GlobalRateLimiter:
    """Token bucket. `acquire()` blocks until a token is available."""

    def __init__(self, rate_per_second: float, burst: int | None = None) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._rate = rate_per_second
        self._capacity = burst or max(int(rate_per_second), 1)
        self._tokens = float(self._capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last) * self._rate
                )
                self._last = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                wait = (cost - self._tokens) / self._rate
                await asyncio.sleep(wait)
