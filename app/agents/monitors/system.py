from __future__ import annotations

import sys
import psutil
import asyncio
import logging

from pathlib import Path

from app.agents.monitors.base import MonitorDaemon
from app.core.events import EventType, WadeEvent, InternalEventBus

logger = logging.getLogger("wade.monitors.system")

DEFAULT_CPU_THRESHOLD   = 85.0
DEFAULT_RAM_THRESHOLD   = 90.0
DEFAULT_DISK_THRESHOLD  = 95.0
DEFAULT_CHECK_INTERVAL  = 60

class SystemMonitor(MonitorDaemon):
    """Monitors CPU, RAM, and Disk; emits SYS_THRESHOLD on breach and MONITOR_STATUS every cycle."""
    name = "system"

    def __init__(
        self,
        event_bus:      InternalEventBus,
        cpu_threshold:  float = DEFAULT_CPU_THRESHOLD,
        ram_threshold:  float = DEFAULT_RAM_THRESHOLD,
        disk_threshold: float = DEFAULT_DISK_THRESHOLD,
        check_interval: int   = DEFAULT_CHECK_INTERVAL,
    ) -> None:
        super().__init__(event_bus)
        self._cpu_threshold  = cpu_threshold
        self._ram_threshold  = ram_threshold
        self._disk_threshold = disk_threshold
        self._check_interval = check_interval
        self._current_vitals = {"cpu": 0.0, "ram": 0.0, "disk": 0.0, "alerts": []}
        self._was_breached   = False

    async def run(self) -> None:
        logger.info(
            "[SYSTEM] Sensor started (cpu>%.0f%%, ram>%.0f%%, disk>%.0f%%)",
            self._cpu_threshold, self._ram_threshold, self._disk_threshold,
        )
        psutil.cpu_percent(interval=None)
        while True:
            await self._update_state()
            await asyncio.sleep(self._check_interval)

    async def _update_state(self) -> None:
        cpu       = psutil.cpu_percent(interval=None)
        ram       = psutil.virtual_memory().percent
        disk_path = Path.home().anchor if sys.platform == "win32" else "/"
        disk      = psutil.disk_usage(disk_path).percent

        alerts: list[str] = []
        if cpu  > self._cpu_threshold:  alerts.append(f"CPU at {cpu:.1f}%")
        if ram  > self._ram_threshold:  alerts.append(f"RAM at {ram:.1f}%")
        if disk > self._disk_threshold: alerts.append(f"Disk at {disk:.1f}%")

        self._current_vitals = {"cpu": cpu, "ram": ram, "disk": disk, "alerts": alerts}

        is_breached_now = bool(alerts)
        is_recovery     = self._was_breached and not is_breached_now

        if is_breached_now and not self._was_breached:
            await self.emit(WadeEvent(
                event_type=EventType.SYS_THRESHOLD,
                payload={"alerts": alerts, "cpu": cpu, "ram": ram, "disk": disk},
                source="monitor:system",
            ))
            logger.warning("[SYSTEM] Threshold breach: %s", alerts)

        await self.emit(WadeEvent(
            event_type=EventType.MONITOR_STATUS,
            payload={
                "cpu": cpu, "ram": ram, "disk": disk,
                "alerts": alerts, "is_recovery": is_recovery,
            },
            source="monitor:system",
        ))

        self._was_breached = is_breached_now

    def get_vitals(self) -> dict:
        """Public accessor for ProactiveEngine to read current system state."""
        return self._current_vitals

    def get_extra_status(self) -> dict:
        return {
            "cpu":  f"{self._current_vitals['cpu']:.1f}%",
            "ram":  f"{self._current_vitals['ram']:.1f}%",
            "disk": f"{self._current_vitals['disk']:.1f}%",
        }