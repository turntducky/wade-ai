import pytest

from unittest.mock import AsyncMock, MagicMock, patch

from app.core.events import EventType, InternalEventBus, WadeEvent

@pytest.fixture
def mock_bus():
    bus = MagicMock(spec=InternalEventBus)
    bus.emit = AsyncMock()
    return bus

@pytest.fixture
def monitor(mock_bus):
    from app.agents.monitors.system import SystemMonitor
    return SystemMonitor(mock_bus, cpu_threshold=90.0, ram_threshold=90.0, disk_threshold=95.0)

@pytest.mark.asyncio
async def test_emits_sys_threshold_on_first_breach(monitor, mock_bus):
    with patch("psutil.cpu_percent", return_value=95.0), \
         patch("psutil.virtual_memory") as mv, \
         patch("psutil.disk_usage") as md:
        mv.return_value.percent = 50.0
        md.return_value.percent = 50.0
        await monitor._update_state()

    events = [c[0][0] for c in mock_bus.emit.call_args_list]
    threshold_events = [e for e in events if e.event_type == EventType.SYS_THRESHOLD]
    assert len(threshold_events) == 1
    assert "CPU" in threshold_events[0].payload["alerts"][0]

@pytest.mark.asyncio
async def test_no_redundant_sys_threshold_on_continued_breach(monitor, mock_bus):
    monitor._was_breached = True  # already in breached state

    with patch("psutil.cpu_percent", return_value=95.0), \
         patch("psutil.virtual_memory") as mv, \
         patch("psutil.disk_usage") as md:
        mv.return_value.percent = 50.0
        md.return_value.percent = 50.0
        await monitor._update_state()

    events = [c[0][0] for c in mock_bus.emit.call_args_list]
    assert not any(e.event_type == EventType.SYS_THRESHOLD for e in events)

@pytest.mark.asyncio
async def test_emits_monitor_status_every_cycle(monitor, mock_bus):
    with patch("psutil.cpu_percent", return_value=20.0), \
         patch("psutil.virtual_memory") as mv, \
         patch("psutil.disk_usage") as md:
        mv.return_value.percent = 30.0
        md.return_value.percent = 40.0
        await monitor._update_state()

    events = [c[0][0] for c in mock_bus.emit.call_args_list]
    status_events = [e for e in events if e.event_type == EventType.MONITOR_STATUS]
    assert len(status_events) == 1
    assert status_events[0].payload["is_recovery"] is False

@pytest.mark.asyncio
async def test_emits_recovery_when_returning_to_normal(monitor, mock_bus):
    monitor._was_breached = True  # was breached, now readings are normal

    with patch("psutil.cpu_percent", return_value=20.0), \
         patch("psutil.virtual_memory") as mv, \
         patch("psutil.disk_usage") as md:
        mv.return_value.percent = 30.0
        md.return_value.percent = 40.0
        await monitor._update_state()

    events = [c[0][0] for c in mock_bus.emit.call_args_list]
    status_events = [e for e in events if e.event_type == EventType.MONITOR_STATUS]
    assert len(status_events) == 1
    assert status_events[0].payload["is_recovery"] is True