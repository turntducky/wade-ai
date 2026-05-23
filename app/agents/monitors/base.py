from __future__ import annotations

import logging

from datetime import datetime
from abc import ABC, abstractmethod

from app.core.events import EventType, WadeEvent, InternalEventBus

logger = logging.getLogger("wade.monitors")

class MonitorDaemon(ABC):
    """Abstract base for all W.A.D.E. monitor daemons."""
    name: str = "unnamed_monitor"

    def __init__(self, event_bus: InternalEventBus) -> None:
        self._bus = event_bus
        self._last_trigger: datetime | None = None

    @abstractmethod
    async def run(self) -> None:
        """Main loop of the monitor. Should run indefinitely."""
        pass

    def get_extra_status(self) -> dict | None:
        """Override in subclasses to expose monitor-specific state in status()."""
        return None

    async def emit(self, event: WadeEvent) -> None:
        """Broadcast an event onto the shared bus."""
        await self._bus.emit(event)

    async def submit_task(self, goal: str, **_task_kwargs) -> None:
        """Emit a TASK_REQUEST event; the orchestrator will create and execute the task."""
        self._last_trigger = datetime.now()
        await self._bus.emit(WadeEvent(
            event_type=EventType.TASK_REQUEST,
            payload={"goal": goal},
            source=f"monitor:{self.name}",
        ))
        logger.info("[MONITOR:%s] Emitted task request: %s", self.name, goal[:80])

class MonitorRegistry:
    """Holds references to all active monitor daemons."""
    def __init__(self) -> None:
        self._monitors: dict[str, MonitorDaemon] = {}

    def register(self, monitor: MonitorDaemon) -> None:
        self._monitors[monitor.name] = monitor
        logger.debug("Registered monitor: %s", monitor.name)

    def get(self, name: str) -> MonitorDaemon | None:
        return self._monitors.get(name)

    def list_names(self) -> list[str]:
        return list(self._monitors.keys())

    def all(self) -> list[MonitorDaemon]:
        return list(self._monitors.values())

    def status(self) -> list[dict]:
        """Return a snapshot of each monitor's current state."""
        return [
            {
                "name":         m.name,
                "running":      True,
                "last_trigger": m._last_trigger.isoformat() if m._last_trigger else None,
                "extra":        m.get_extra_status(),
            }
            for m in self._monitors.values()
        ]