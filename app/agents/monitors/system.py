from __future__ import annotations

import sys
import psutil
import asyncio
import logging

from collections import deque
from pathlib import Path

from app.agents.monitors.base import MonitorDaemon
from app.core.events import EventType, WadeEvent, InternalEventBus

logger = logging.getLogger("wade.monitors.system")

DEFAULT_CPU_THRESHOLD  = 85.0
DEFAULT_RAM_THRESHOLD  = 90.0
DEFAULT_DISK_THRESHOLD = 95.0
DEFAULT_CHECK_INTERVAL = 60

_TREND_WINDOW          = 5    # readings in the rolling window
_TREND_SLOPE_THRESHOLD = 2.0  # minimum %/reading slope to qualify as "rising"
_TREND_PROXIMITY       = 0.85 # within 85% of threshold before we warn


class SystemMonitor(MonitorDaemon):
    """Monitors CPU, RAM, and Disk; emits SYS_THRESHOLD on breach or trend, MONITOR_STATUS every cycle."""
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
        self._current_vitals = {
            "cpu": 0.0, "ram": 0.0, "disk": 0.0,
            "alerts": [], "top_processes": [],
        }
        self._was_breached = False
        self._trend_warned = False
        self._cpu_history: deque[float] = deque(maxlen=_TREND_WINDOW)
        self._ram_history: deque[float] = deque(maxlen=_TREND_WINDOW)

    async def run(self) -> None:
        logger.info(
            "[SYSTEM] Sensor started (cpu>%.0f%%, ram>%.0f%%, disk>%.0f%%)",
            self._cpu_threshold, self._ram_threshold, self._disk_threshold,
        )
        psutil.cpu_percent(interval=None)
        while True:
            await self._update_state()
            await asyncio.sleep(self._check_interval)

    def _get_top_processes(self, n: int = 5) -> list[dict]:
        """Return top n processes by CPU usage with name and memory info."""
        try:
            procs = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                try:
                    info = p.info
                    if info["cpu_percent"] is None:
                        info["cpu_percent"] = 0.0
                    procs.append(info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            procs.sort(key=lambda x: x.get("cpu_percent") or 0.0, reverse=True)
            return [
                {
                    "pid":  p["pid"],
                    "name": p["name"],
                    "cpu":  round(p.get("cpu_percent") or 0.0, 1),
                    "mem":  round(p.get("memory_percent") or 0.0, 1),
                }
                for p in procs[:n]
            ]
        except Exception:
            return []

    def _detect_trend(self, history: deque[float], threshold: float) -> bool:
        """Return True if metric is steadily rising toward threshold but hasn't crossed it yet."""
        if len(history) < _TREND_WINDOW:
            return False
        values = list(history)
        rising = all(values[i] < values[i + 1] for i in range(len(values) - 1))
        if not rising:
            return False
        slope = (values[-1] - values[0]) / len(values)
        latest = values[-1]
        return (
            slope >= _TREND_SLOPE_THRESHOLD
            and latest >= threshold * _TREND_PROXIMITY
            and latest < threshold
        )

    async def _update_state(self) -> None:
        cpu  = psutil.cpu_percent(interval=None)
        ram  = psutil.virtual_memory().percent
        disk = psutil.disk_usage(Path.home().anchor if sys.platform == "win32" else "/").percent

        self._cpu_history.append(cpu)
        self._ram_history.append(ram)

        alerts: list[str] = []
        if cpu  > self._cpu_threshold:  alerts.append(f"CPU at {cpu:.1f}%")
        if ram  > self._ram_threshold:  alerts.append(f"RAM at {ram:.1f}%")
        if disk > self._disk_threshold: alerts.append(f"Disk at {disk:.1f}%")

        top_procs = self._get_top_processes() if alerts else []
        self._current_vitals = {
            "cpu": cpu, "ram": ram, "disk": disk,
            "alerts": alerts, "top_processes": top_procs,
        }

        is_breached_now = bool(alerts)
        is_recovery     = self._was_breached and not is_breached_now

        if is_breached_now and not self._was_breached:
            await self.emit(WadeEvent(
                event_type=EventType.SYS_THRESHOLD,
                payload={
                    "alerts": alerts,
                    "cpu": cpu, "ram": ram, "disk": disk,
                    "top_processes": self._get_top_processes(),
                    "is_trend": False,
                },
                source="monitor:system",
            ))
            logger.warning("[SYSTEM] Threshold breach: %s", alerts)
            self._trend_warned = False

        elif not is_breached_now and not self._trend_warned:
            cpu_trend = self._detect_trend(self._cpu_history, self._cpu_threshold)
            ram_trend = self._detect_trend(self._ram_history, self._ram_threshold)
            if cpu_trend or ram_trend:
                trend_alerts = []
                if cpu_trend:
                    slope = (list(self._cpu_history)[-1] - list(self._cpu_history)[0]) / _TREND_WINDOW
                    trend_alerts.append(f"CPU climbing ({cpu:.1f}%, +{slope:.1f}%/min)")
                if ram_trend:
                    trend_alerts.append(f"RAM climbing ({ram:.1f}%)")
                await self.emit(WadeEvent(
                    event_type=EventType.SYS_THRESHOLD,
                    payload={
                        "alerts": trend_alerts,
                        "cpu": cpu, "ram": ram, "disk": disk,
                        "top_processes": self._get_top_processes(),
                        "is_trend": True,
                    },
                    source="monitor:system",
                ))
                self._trend_warned = True
                logger.info("[SYSTEM] Trend warning emitted: %s", trend_alerts)

        if is_recovery:
            self._trend_warned = False

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
        return self._current_vitals

    def get_extra_status(self) -> dict:
        return {
            "cpu":  f"{self._current_vitals['cpu']:.1f}%",
            "ram":  f"{self._current_vitals['ram']:.1f}%",
            "disk": f"{self._current_vitals['disk']:.1f}%",
        }
