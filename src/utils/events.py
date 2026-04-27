"""In-process pub/sub for the dashboard's SSE stream.

Agents publish state changes (`event_bus.publish({...})`); the dashboard
subscribes once per HTTP client and forwards everything as Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    """Tiny async fan-out. Subscribers each get their own queue.

    Backpressure: queues are bounded (256). If a slow subscriber overflows,
    its oldest item is dropped — the live dashboard prefers freshness over
    completeness; persistent state lives in the DB.
    """

    def __init__(self, queue_size: int = 256) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._queue_size = queue_size
        self._lock = asyncio.Lock()

    async def subscribe(self) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(q)
        try:
            while True:
                msg = await q.get()
                yield msg
        finally:
            async with self._lock:
                self._subscribers.discard(q)

    async def publish(self, event: dict[str, Any]) -> None:
        msg = json.dumps(event, ensure_ascii=False, default=str)
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


_global_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus
