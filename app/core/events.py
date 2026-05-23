from __future__ import annotations

import time
import asyncio
import logging

from enum import Enum
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger("wade.events")

class EventType(str, Enum):
    FS_CHANGE       = "fs.change"
    SYS_THRESHOLD   = "sys.threshold"
    BUILD_LOG       = "build.log"
    USER_ARRIVAL    = "user.arrival"
    CALENDAR_UPDATE = "calendar.update"
    MONITOR_STATUS  = "monitor.status"
    TASK_REQUEST    = "task.request"

@dataclass
class WadeEvent:
    event_type: EventType
    payload:    dict[str, Any]
    source:     str
    timestamp:  float = field(default_factory=time.time)

Handler = Callable[["WadeEvent"], Coroutine[Any, Any, None]]

class InternalEventBus:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[WadeEvent] = asyncio.Queue()
        self._handlers: dict[EventType, list[Handler]] = {}
        self._running = False
        self._recent: deque[WadeEvent] = deque(maxlen=20)

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    async def emit(self, event: WadeEvent) -> None:
        await self._queue.put(event)

    def emit_nowait(self, event: WadeEvent) -> None:
        self._queue.put_nowait(event)

    async def start(self) -> None:
        self._running = True
        await self._dispatch_loop()

    def stop(self) -> None:
        self._running = False

    def get_recent_state(self, n: int = 5) -> list[dict[str, Any]]:
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

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            self._recent.append(event)
            handlers = self._handlers.get(event.event_type, [])
            for handler in handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.exception("[BUS] Handler error for %s", event.event_type)
            self._queue.task_done()