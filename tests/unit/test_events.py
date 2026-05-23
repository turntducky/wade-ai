import time
import pytest
import asyncio

from app.core.events import EventType, WadeEvent, InternalEventBus

def test_wade_event_timestamp_is_set_automatically():
    before = time.time()
    event = WadeEvent(event_type=EventType.MONITOR_STATUS, payload={}, source="test")
    after = time.time()
    assert before <= event.timestamp <= after

def test_event_type_string_values():
    assert EventType.FS_CHANGE       == "fs.change"
    assert EventType.SYS_THRESHOLD   == "sys.threshold"
    assert EventType.TASK_REQUEST    == "task.request"
    assert EventType.MONITOR_STATUS  == "monitor.status"

def test_emit_nowait_queues_event():
    bus = InternalEventBus()
    event = WadeEvent(event_type=EventType.FS_CHANGE, payload={"name": "foo.md"}, source="fs")
    bus.emit_nowait(event)
    assert bus._queue.qsize() == 1

@pytest.mark.asyncio
async def test_subscribe_and_receive_event():
    bus = InternalEventBus()
    received = []

    async def handler(event: WadeEvent):
        received.append(event)

    bus.subscribe(EventType.MONITOR_STATUS, handler)
    bus.emit_nowait(WadeEvent(event_type=EventType.MONITOR_STATUS, payload={"ok": True}, source="t"))

    loop_task = asyncio.create_task(bus.start())
    await asyncio.sleep(0.05)
    bus.stop()
    try:
        await asyncio.wait_for(loop_task, timeout=0.5)
    except asyncio.TimeoutError:
        loop_task.cancel()

    assert len(received) == 1
    assert received[0].payload == {"ok": True}

@pytest.mark.asyncio
async def test_unregistered_event_type_does_not_raise():
    bus = InternalEventBus()
    bus.emit_nowait(WadeEvent(event_type=EventType.USER_ARRIVAL, payload={}, source="test"))

    loop_task = asyncio.create_task(bus.start())
    await asyncio.sleep(0.05)
    bus.stop()
    try:
        await asyncio.wait_for(loop_task, timeout=0.5)
    except asyncio.TimeoutError:
        loop_task.cancel()

@pytest.mark.asyncio
async def test_handler_exception_does_not_crash_bus():
    bus = InternalEventBus()
    calls = []

    async def bad_handler(event):
        raise ValueError("oops")

    async def good_handler(event):
        calls.append(event)

    bus.subscribe(EventType.SYS_THRESHOLD, bad_handler)
    bus.subscribe(EventType.SYS_THRESHOLD, good_handler)
    bus.emit_nowait(WadeEvent(event_type=EventType.SYS_THRESHOLD, payload={}, source="test"))

    loop_task = asyncio.create_task(bus.start())
    await asyncio.sleep(0.05)
    bus.stop()
    try:
        await asyncio.wait_for(loop_task, timeout=0.5)
    except asyncio.TimeoutError:
        loop_task.cancel()

    assert len(calls) == 1