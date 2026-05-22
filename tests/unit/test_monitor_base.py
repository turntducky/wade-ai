import pytest
import asyncio

from unittest.mock import AsyncMock, MagicMock

from app.core.events import EventType, WadeEvent, InternalEventBus
from app.agents.monitors.base import MonitorDaemon, MonitorRegistry

class ConcreteMonitor(MonitorDaemon):
    name = "test_monitor"

    async def run(self) -> None:
        await asyncio.sleep(0.01)

@pytest.fixture
def mock_bus():
    bus = MagicMock(spec=InternalEventBus)
    bus.emit = AsyncMock()
    return bus

@pytest.mark.asyncio
async def test_submit_task_emits_task_request_event(mock_bus):
    monitor = ConcreteMonitor(mock_bus)
    await monitor.submit_task("do something")
    mock_bus.emit.assert_called_once()
    event: WadeEvent = mock_bus.emit.call_args[0][0]
    assert event.event_type == EventType.TASK_REQUEST
    assert event.payload["goal"] == "do something"
    assert event.source == "monitor:test_monitor"

@pytest.mark.asyncio
async def test_submit_task_sets_last_trigger(mock_bus):
    monitor = ConcreteMonitor(mock_bus)
    assert monitor._last_trigger is None
    await monitor.submit_task("ping")
    assert monitor._last_trigger is not None

@pytest.mark.asyncio
async def test_emit_delegates_to_bus(mock_bus):
    monitor = ConcreteMonitor(mock_bus)
    event = WadeEvent(event_type=EventType.MONITOR_STATUS, payload={}, source="test")
    await monitor.emit(event)
    mock_bus.emit.assert_called_once_with(event)

@pytest.mark.asyncio
async def test_monitor_daemon_run_executes(mock_bus):
    monitor = ConcreteMonitor(mock_bus)
    fut = asyncio.ensure_future(monitor.run())
    done, _ = await asyncio.wait([fut], timeout=1.0)
    if not done:
        fut.cancel()
        try:
            await fut
        except (asyncio.CancelledError, Exception):
            pass
        raise asyncio.TimeoutError()
    await fut

def test_monitor_registry_register_and_list(mock_bus):
    registry = MonitorRegistry()
    m = ConcreteMonitor(mock_bus)
    registry.register(m)
    assert "test_monitor" in registry.list_names()

def test_monitor_registry_get(mock_bus):
    registry = MonitorRegistry()
    m = ConcreteMonitor(mock_bus)
    registry.register(m)
    assert registry.get("test_monitor") is m

def test_monitor_registry_get_missing_returns_none():
    registry = MonitorRegistry()
    assert registry.get("nonexistent") is None