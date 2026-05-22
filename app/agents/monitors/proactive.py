from __future__ import annotations

import asyncio
import logging

from app.agents.monitors.base import MonitorDaemon
from app.services.proactive import proactive_engine
from app.core.events import EventType, WadeEvent, InternalEventBus

logger = logging.getLogger("wade.monitors.proactive")

class ProactiveMonitor(MonitorDaemon):
    """Watches for system resource alerts and filesystem changes, and uses the ProactiveEngine to decide when to inject helpful tasks."""
    name = "proactive"

    def __init__(self, event_bus: InternalEventBus, task_store=None) -> None:
        super().__init__(event_bus)
        if task_store is not None:
            proactive_engine.bind_task_store(task_store)
        event_bus.subscribe(EventType.SYS_THRESHOLD, self._on_sys_threshold)
        event_bus.subscribe(EventType.FS_CHANGE,     self._on_fs_change)

    def set_inference_fn(self, fn) -> None:
        proactive_engine.set_inference_fn(fn)

    def notify_user_active(self) -> None:
        proactive_engine.notify_user_active()

    async def register(self, q: asyncio.Queue) -> None:
        await proactive_engine.register(q)

    async def unregister(self, q: asyncio.Queue) -> None:
        await proactive_engine.unregister(q)

    async def _on_sys_threshold(self, event: WadeEvent) -> None:
        alerts = event.payload.get("alerts", [])
        if not alerts:
            return
        goal = (
            f"System resource alert: {', '.join(alerts)}. "
            "Check active processes and advise the user on what is consuming resources."
        )
        await self.submit_task(goal)
        logger.info("[PROACTIVE] Injected task for system alert: %s", alerts)

    async def _on_fs_change(self, event: WadeEvent) -> None:
        name       = event.payload.get("name",       "unknown")
        event_type = event.payload.get("event_type", "changed")
        proactive_engine.record_fs_event(name, event_type)
        logger.debug("[PROACTIVE] Recorded fs event: %s %s", event_type, name)

    async def run(self) -> None:
        logger.info("[PROACTIVE] Monitor routing to ProactiveEngine...")
        await proactive_engine.run()

    def get_extra_status(self) -> dict:
        from app.services.proactive import COOLDOWN_MINUTES, IDLE_CHECK_MINUTES, MAX_PER_HOUR
        return {
            "cooldown_minutes":   COOLDOWN_MINUTES,
            "idle_check_minutes": IDLE_CHECK_MINUTES,
            "max_per_hour":       MAX_PER_HOUR,
        }

proactive_monitor = ProactiveMonitor.__new__(ProactiveMonitor)