from __future__ import annotations

import asyncio
import logging

from app.agents.monitors.base import MonitorDaemon
from app.services.proactive import proactive_engine
from app.core.events import EventType, WadeEvent, InternalEventBus

logger = logging.getLogger("wade.monitors.proactive")

class ProactiveMonitor(MonitorDaemon):
    """Routes bus events to the ProactiveEngine and injects tasks for critical signals."""
    name = "proactive"

    def __init__(self, event_bus: InternalEventBus, task_store=None) -> None:
        super().__init__(event_bus)
        if task_store is not None:
            proactive_engine.bind_task_store(task_store)
        proactive_engine.bind_bus(event_bus)

        event_bus.subscribe(EventType.SYS_THRESHOLD,   self._on_sys_threshold)
        event_bus.subscribe(EventType.FS_CHANGE,        self._on_fs_change)
        event_bus.subscribe(EventType.BUILD_LOG,        self._on_build_log)
        event_bus.subscribe(EventType.USER_ARRIVAL,     self._on_user_arrival)

    def set_inference_fn(self, fn) -> None:
        proactive_engine.set_inference_fn(fn)

    def notify_user_active(self) -> None:
        proactive_engine.notify_user_active()

    async def register(self, q: asyncio.Queue) -> None:
        await proactive_engine.register(q)

    async def unregister(self, q: asyncio.Queue) -> None:
        await proactive_engine.unregister(q)

    async def _on_sys_threshold(self, event: WadeEvent) -> None:
        alerts      = event.payload.get("alerts", [])
        is_trend    = event.payload.get("is_trend", False)
        top_procs   = event.payload.get("top_processes", [])

        if not alerts:
            return

        proc_summary = ""
        if top_procs:
            lines = [f"{p['name']} (PID {p['pid']}): CPU {p['cpu']}%, MEM {p['mem']}%" for p in top_procs[:3]]
            proc_summary = " Top processes: " + "; ".join(lines) + "."

        severity = "trending toward threshold" if is_trend else "breached threshold"
        goal = (
            f"System resource {severity}: {', '.join(alerts)}.{proc_summary} "
            "Advise the user concisely on what is consuming resources and suggest remediation."
        )
        await self.submit_task(goal)
        logger.info("[PROACTIVE] Injected system task (%s): %s", severity, alerts)

    async def _on_fs_change(self, event: WadeEvent) -> None:
        name       = event.payload.get("name", "unknown")
        event_type = event.payload.get("event_type", "changed")
        proactive_engine.record_fs_event(name, event_type)
        logger.debug("[PROACTIVE] Recorded fs event: %s %s", event_type, name)

    async def _on_build_log(self, event: WadeEvent) -> None:
        path = event.payload.get("path", "unknown file")
        tail = event.payload.get("tail", "")
        goal = (
            f"Build error detected in {path}. "
            f"Log tail: {tail[:400]}. "
            "Diagnose what failed and suggest a concrete fix."
        )
        await self.submit_task(goal)
        logger.info("[PROACTIVE] Injected task for build error: %s", path)

    async def _on_user_arrival(self, event: WadeEvent) -> None:
        idle_minutes = event.payload.get("idle_minutes", 0)
        await proactive_engine.on_user_arrival(idle_minutes)
        logger.info("[PROACTIVE] User arrival after %.0f min idle.", idle_minutes)

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
