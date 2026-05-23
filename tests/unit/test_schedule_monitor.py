import pytest
import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture
def monitor():
    from app.agents.monitors.schedule import ScheduleMonitor
    from app.core.events import InternalEventBus
    mock_bus = MagicMock(spec=InternalEventBus)
    mock_bus.emit = AsyncMock()
    return ScheduleMonitor(mock_bus)

def test_add_job_returns_job_id(monitor):
    job_id = monitor.add_job("daily report", trigger="interval", seconds=3600)
    assert isinstance(job_id, str)
    assert len(job_id) > 0

def test_add_job_stores_in_scheduler(monitor):
    monitor.add_job("cleanup", trigger="cron", hour=0, minute=0)
    jobs = monitor.list_jobs()
    assert len(jobs) >= 1

def test_remove_job(monitor):
    job_id = monitor.add_job("temp job", trigger="interval", seconds=60)
    assert len(monitor.list_jobs()) >= 1
    monitor.remove_job(job_id)
    remaining_ids = [j["id"] for j in monitor.list_jobs()]
    assert job_id not in remaining_ids

@pytest.mark.asyncio
async def test_fire_submits_task_to_orchestrator(monitor):
    await monitor._fire("do the thing", job_id="test-job-1")
    monitor._bus.emit.assert_called_once()
    event = monitor._bus.emit.call_args[0][0]
    assert event.payload["goal"] == "do the thing"
    assert "schedule" in event.source