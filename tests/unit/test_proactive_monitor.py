import pytest
import asyncio

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.proactive import proactive_engine
from app.core.events import EventType, WadeEvent, InternalEventBus

@pytest.fixture
def mock_bus():
    bus = MagicMock(spec=InternalEventBus)
    bus.emit      = AsyncMock()
    bus.subscribe = MagicMock()
    return bus

@pytest.fixture
def mock_task_store():
    store = MagicMock()
    store.list_active.return_value = []
    return store

@pytest.fixture(autouse=True)
def reset_engine():
    """Reset shared proactive_engine state between tests."""
    proactive_engine._clients            = []
    proactive_engine._last_sent          = None
    proactive_engine._user_last_active   = None
    proactive_engine._pending_fs_events  = {}
    proactive_engine._sent_count         = 0
    if hasattr(proactive_engine, "_task_store"):
        del proactive_engine._task_store
    yield

@pytest.fixture
def monitor(mock_bus, mock_task_store):
    from app.agents.monitors.proactive import ProactiveMonitor
    return ProactiveMonitor(mock_bus, task_store=mock_task_store)

def test_init_binds_task_store(mock_bus, mock_task_store):
    from app.agents.monitors.proactive import ProactiveMonitor
    ProactiveMonitor(mock_bus, task_store=mock_task_store)
    assert proactive_engine._task_store is mock_task_store

def test_init_subscribes_to_sys_threshold_and_fs_change(mock_bus, mock_task_store):
    from app.agents.monitors.proactive import ProactiveMonitor
    ProactiveMonitor(mock_bus, task_store=mock_task_store)
    subscribed_types = [call[0][0] for call in mock_bus.subscribe.call_args_list]
    assert EventType.SYS_THRESHOLD in subscribed_types
    assert EventType.FS_CHANGE     in subscribed_types

@pytest.mark.asyncio
async def test_on_sys_threshold_emits_task_request(monitor, mock_bus):
    event = WadeEvent(
        event_type=EventType.SYS_THRESHOLD,
        payload={"alerts": ["CPU at 95.0%"], "cpu": 95.0, "ram": 50.0, "disk": 40.0},
        source="monitor:system",
    )
    await monitor._on_sys_threshold(event)
    mock_bus.emit.assert_called_once()
    emitted: WadeEvent = mock_bus.emit.call_args[0][0]
    assert emitted.event_type == EventType.TASK_REQUEST
    assert "cpu" in emitted.payload["goal"].lower()

@pytest.mark.asyncio
async def test_on_sys_threshold_no_op_for_empty_alerts(monitor, mock_bus):
    event = WadeEvent(
        event_type=EventType.SYS_THRESHOLD,
        payload={"alerts": [], "cpu": 20.0, "ram": 30.0, "disk": 40.0},
        source="monitor:system",
    )
    await monitor._on_sys_threshold(event)
    mock_bus.emit.assert_not_called()

@pytest.mark.asyncio
async def test_on_fs_change_records_in_engine(monitor):
    event = WadeEvent(
        event_type=EventType.FS_CHANGE,
        payload={"name": "notes.md", "event_type": "modified"},
        source="monitor:filesystem",
    )
    await monitor._on_fs_change(event)
    assert "notes.md" in proactive_engine._pending_fs_events

@pytest.mark.asyncio
async def test_register_and_unregister_client(monitor):
    q = asyncio.Queue()
    await monitor.register(q)
    assert len(proactive_engine._clients) == 1
    await monitor.unregister(q)
    assert len(proactive_engine._clients) == 0

def test_notify_user_active_sets_timestamp(monitor):
    monitor.notify_user_active()
    assert proactive_engine._user_last_active is not None

def test_can_send_routine_false_within_cooldown():
    proactive_engine._last_sent = datetime.now()
    assert proactive_engine._can_send_routine() is False

def test_can_send_routine_true_after_cooldown():
    proactive_engine._last_sent = datetime.now() - timedelta(minutes=30)
    assert proactive_engine._can_send_routine() is True

def test_record_fs_event_accumulates():
    proactive_engine.record_fs_event("foo.md", "created")
    proactive_engine.record_fs_event("bar.md", "modified")
    assert len(proactive_engine._pending_fs_events) == 2

def test_pop_fs_events_returns_and_clears():
    proactive_engine._pending_fs_events = {"notes.md": "modified", "plan.md": "created"}
    result = proactive_engine._pop_fs_events()
    assert len(result) == 2
    assert proactive_engine._pending_fs_events == {}

@pytest.mark.asyncio
async def test_broadcast_pushes_to_all_clients(monitor):
    q1, q2 = asyncio.Queue(), asyncio.Queue()
    await monitor.register(q1)
    await monitor.register(q2)
    with patch("app.services.proactive.append_to_memory"):
        await proactive_engine._broadcast("hello")
    msg1 = await q1.get()
    msg2 = await q2.get()
    assert msg1["content"] == "hello"
    assert msg2["type"]    == "proactive_message"