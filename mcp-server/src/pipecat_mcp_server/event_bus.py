"""Simple async SSE event bus for daemon state changes.

One global bus per process. Producers call ``emit(event, data)``; the SSE
endpoint iterates ``subscribe()`` to stream events to each connected browser.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str
    data: dict[str, Any]

    def sse(self) -> str:
        """Format as an SSE text block (two trailing newlines included)."""
        return f"event: {self.type}\ndata: {json.dumps(self.data)}\n\n"


class EventBus:
    """Broadcast events to zero or more SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._lock = asyncio.Lock()

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Push an event to every active subscriber."""
        ev = Event(type=event_type, data=data)
        async with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    pass  # slow consumer — drop

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        """Context manager that yields a queue of events.

        The queue is automatically removed on exit.
        """
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers.append(q)
        try:
            yield q
        finally:
            async with self._lock:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass


# Module-level singleton — same pattern as voice_channel in server.py.
event_bus = EventBus()
