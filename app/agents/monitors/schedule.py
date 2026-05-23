from __future__ import annotations

import uuid
import asyncio
import logging

from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.agents.monitors.base import MonitorDaemon
from app.core.events import InternalEventBus

logger = logging.getLogger("wade.monitors.schedule")

class ScheduleMonitor(MonitorDaemon):
    """Submits tasks on a schedule using APScheduler."""
    name = "schedule"

    def __init__(self, event_bus: InternalEventBus) -> None:
        super().__init__(event_bus)
        self._scheduler = AsyncIOScheduler()
        self._jobs: dict[str, str] = {}

    def add_job(self, goal: str, trigger: str, **trigger_kwargs: Any) -> str:
        job_id = str(uuid.uuid4())
        self._scheduler.add_job(
            self._fire,
            trigger=trigger,
            kwargs={"goal": goal, "job_id": job_id},
            id=job_id,
            **trigger_kwargs,
        )
        self._jobs[job_id] = goal
        logger.info("[SCHEDULE] Added job '%s' (%s)", goal[:60], trigger)
        return job_id

    def remove_job(self, job_id: str) -> None:
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        self._jobs.pop(job_id, None)

    def get_extra_status(self) -> dict:
        raw = self._scheduler.get_jobs()
        datetimes = [j.next_run_time for j in raw if j.next_run_time is not None]
        next_run = str(min(datetimes)) if datetimes else None
        return {"job_count": len(raw), "next_run": next_run}

    def list_jobs(self) -> list[dict]:
        result = []
        for j in self._scheduler.get_jobs():
            try:
                next_run = str(j.next_run_time)
            except AttributeError:
                next_run = "pending"
            result.append({"id": j.id, "goal": self._jobs.get(j.id, ""), "next_run": next_run})
        return result

    async def _fire(self, goal: str, job_id: str) -> None:
        logger.info("[SCHEDULE] Firing job '%s'", goal[:60])
        await self.submit_task(goal)

    async def run(self) -> None:
        logger.info("[SCHEDULE] Monitor started.")
        self._scheduler.start()
        try:
            while True:
                await asyncio.sleep(60)
        finally:
            self._scheduler.shutdown(wait=False)
            logger.info("[SCHEDULE] Monitor stopped.")