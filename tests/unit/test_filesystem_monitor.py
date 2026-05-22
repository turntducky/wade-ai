import pytest

from unittest.mock import AsyncMock, MagicMock

from app.core.events import EventType, InternalEventBus

@pytest.fixture
def mock_bus():
    bus = MagicMock(spec=InternalEventBus)
    bus.emit = AsyncMock()
    return bus

@pytest.fixture
def monitor(mock_bus, tmp_path):
    from app.agents.monitors.filesystem import FilesystemMonitor
    return FilesystemMonitor(mock_bus, watch_dir=tmp_path)

def test_filesystem_monitor_watch_dir(monitor, tmp_path):
    assert monitor._watch_dir == tmp_path

@pytest.mark.asyncio
async def test_on_fs_event_emits_fs_change(monitor, mock_bus, tmp_path):
    test_file = tmp_path / "notes.txt"
    await monitor._on_fs_event(str(test_file), "created")

    mock_bus.emit.assert_called_once()
    event = mock_bus.emit.call_args[0][0]
    assert event.event_type == EventType.FS_CHANGE
    assert event.payload["name"]       == "notes.txt"
    assert event.payload["event_type"] == "created"

@pytest.mark.asyncio
async def test_on_fs_event_modified_emits_event(monitor, mock_bus, tmp_path):
    test_file = tmp_path / "existing.txt"
    await monitor._on_fs_event(str(test_file), "modified")

    mock_bus.emit.assert_called_once()
    event = mock_bus.emit.call_args[0][0]
    assert event.event_type            == EventType.FS_CHANGE
    assert event.payload["event_type"] == "modified"

@pytest.mark.asyncio
async def test_ignores_hidden_files(monitor, mock_bus, tmp_path):
    hidden = tmp_path / ".hidden"
    await monitor._on_fs_event(str(hidden), "created")
    mock_bus.emit.assert_not_called()

@pytest.mark.asyncio
async def test_ignores_daily_memory_files(monitor, mock_bus, tmp_path):
    mem_file = tmp_path / "04-12-26.md"
    await monitor._on_fs_event(str(mem_file), "created")
    mock_bus.emit.assert_not_called()