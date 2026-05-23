from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

def _make_bus() -> MagicMock:
    """Return a MagicMock InternalEventBus whose emit() is awaitable."""
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus

@pytest.fixture
def schedule_monitor():
    from app.agents.monitors.schedule import ScheduleMonitor
    return ScheduleMonitor(_make_bus())

@pytest.fixture
def system_monitor():
    from app.agents.monitors.system import SystemMonitor
    return SystemMonitor(_make_bus(), cpu_threshold=90.0, ram_threshold=90.0)

@pytest.mark.asyncio
async def test_fire_calls_submit_with_correct_goal(schedule_monitor):
    """_fire() must emit a TASK_REQUEST event whose payload.goal matches."""
    from app.core.events import EventType

    await schedule_monitor._fire("run daily digest", job_id="job-001")

    schedule_monitor._bus.emit.assert_called_once()
    event = schedule_monitor._bus.emit.call_args[0][0]
    assert event.event_type == EventType.TASK_REQUEST
    assert event.payload["goal"] == "run daily digest"
    assert "schedule" in event.source

@pytest.mark.asyncio
async def test_system_monitor_submits_on_high_cpu(system_monitor):
    """_update_state() must emit SYS_THRESHOLD when CPU exceeds threshold."""
    from app.core.events import EventType

    with patch("psutil.cpu_percent", return_value=95.0):
        with patch("psutil.virtual_memory") as mock_mem:
            mock_mem.return_value.percent = 50.0
            with patch("psutil.disk_usage") as mock_disk:
                mock_disk.return_value.percent = 10.0
                await system_monitor._update_state()

    assert system_monitor._bus.emit.called
    events = [call[0][0] for call in system_monitor._bus.emit.call_args_list]
    threshold_events = [e for e in events if e.event_type == EventType.SYS_THRESHOLD]
    assert threshold_events, "Expected at least one SYS_THRESHOLD event"
    payload = threshold_events[0].payload
    alerts_str = " ".join(payload.get("alerts", []))
    assert "cpu" in alerts_str.lower() or "CPU" in alerts_str

@pytest.mark.asyncio
async def test_system_monitor_submits_on_high_ram(system_monitor):
    """_update_state() must emit SYS_THRESHOLD when RAM exceeds threshold."""
    from app.core.events import EventType

    with patch("psutil.cpu_percent", return_value=30.0):
        with patch("psutil.virtual_memory") as mock_mem:
            mock_mem.return_value.percent = 95.0
            with patch("psutil.disk_usage") as mock_disk:
                mock_disk.return_value.percent = 10.0
                await system_monitor._update_state()

    assert system_monitor._bus.emit.called
    events = [call[0][0] for call in system_monitor._bus.emit.call_args_list]
    threshold_events = [e for e in events if e.event_type == EventType.SYS_THRESHOLD]
    assert threshold_events, "Expected at least one SYS_THRESHOLD event"
    payload = threshold_events[0].payload
    alerts_str = " ".join(payload.get("alerts", []))
    assert "ram" in alerts_str.lower() or "RAM" in alerts_str or "mem" in alerts_str.lower()

@pytest.mark.asyncio
async def test_system_monitor_run_cancels_cleanly(system_monitor):
    """run() infinite loop must exit cleanly when the task is cancelled."""
    with patch("psutil.cpu_percent", return_value=20.0):
        with patch("psutil.virtual_memory") as mock_mem:
            mock_mem.return_value.percent = 20.0
            with patch("psutil.disk_usage") as mock_disk:
                mock_disk.return_value.percent = 10.0
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    task = asyncio.create_task(system_monitor.run())
                    await asyncio.sleep(0)   # let the task start
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass  # expected
    assert task.cancelled() or task.done()

@pytest.mark.asyncio
async def test_schedule_monitor_run_cancels_cleanly(schedule_monitor):
    """ScheduleMonitor.run() must exit cleanly when cancelled."""
    schedule_monitor._scheduler.start = MagicMock()
    schedule_monitor._scheduler.shutdown = MagicMock()

    original_sleep = asyncio.sleep

    async def fast_sleep(_delay):
        await original_sleep(0)

    with patch("asyncio.sleep", side_effect=fast_sleep):
        run_task = asyncio.create_task(schedule_monitor.run())
        await original_sleep(0)
        await original_sleep(0)
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
    assert run_task.done()
    schedule_monitor._scheduler.shutdown.assert_called_once()