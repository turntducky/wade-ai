from __future__ import annotations

import time
import asyncio
import logging
import itertools

from enum import Enum
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger("wade.events")

_seq = itertools.count()

class EventType(str, Enum):
    FS_CHANGE       = "fs.change"
    SYS_THRESHOLD   = "sys.threshold"
    BUILD_LOG       = "build.log"
    USER_ARRIVAL    = "user.arrival"
    CALENDAR_UPDATE = "calendar.update"
    MONITOR_STATUS  = "monitor.status"
    TASK_REQUEST    = "task.request"

# Lower number = higher priority in the dispatch queue
_PRIORITY: dict[EventType, int] = {
    EventType.SYS_THRESHOLD:   0,
    EventType.BUILD_LOG:        0,
    EventType.TASK_REQUEST:     1,
    EventType.FS_CHANGE:        1,
    EventType.USER_ARRIVAL:     1,
    EventType.CALENDAR_UPDATE:  2,
    EventType.MONITOR_STATUS:   3,
}

@dataclass
class WadeEvent:
    event_type: EventType
    payload:    dict[str, Any]
    source:     str
    timestamp:  float = field(default_factory=time.time)

Handler = Callable[["WadeEvent"], Coroutine[Any, Any, None]]

class InternalEventBus:
    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._handlers: dict[EventType, list[Handler]] = {}
        self._running = False
        self._recent: deque[WadeEvent] = deque(maxlen=100)

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    async def emit(self, event: WadeEvent) -> None:
        priority = _PRIORITY.get(event.event_type, 1)
        await self._queue.put((priority, next(_seq), event))

    def emit_nowait(self, event: WadeEvent) -> None:
        priority = _PRIORITY.get(event.event_type, 1)
        self._queue.put_nowait((priority, next(_seq), event))

    async def start(self) -> None:
        self._running = True
        await self._dispatch_loop()

    def stop(self) -> None:
        self._running = False

    def get_recent_state(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the last n dispatched events as serializable dicts."""
        events = list(self._recent)[-n:]
        return [
            {
                "type": e.event_type.value,
                "source": e.source,
                "payload": e.payload,
                "timestamp": e.timestamp,
            }
            for e in events
        ]

    def get_rolling_summary(self) -> dict[str, Any]:
        """Return grouped counts and latest payload per event type — richer than get_recent_state for prompt grounding."""
        counts: dict[str, int] = {}
        latest: dict[str, dict] = {}
        for e in self._recent:
            t = e.event_type.value
            counts[t] = counts.get(t, 0) + 1
            latest[t] = e.payload
        return {"counts": counts, "latest": latest}

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                _priority, _seq_n, event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            self._recent.append(event)
            handlers = self._handlers.get(event.event_type, [])
            if handlers:
                results = await asyncio.gather(
                    *[handler(event) for handler in handlers],
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, Exception):
                        logger.exception("[BUS] Handler error for %s: %s", event.event_type, r)
            self._queue.task_done()
