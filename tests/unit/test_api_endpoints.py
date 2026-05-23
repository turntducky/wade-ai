import pytest

from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.memory.episodes import Episode, EpisodeStore
from app.core.task_store import Task, TaskStatus, TaskStore
from app.agents.monitors.base import MonitorDaemon, MonitorRegistry

@pytest.fixture
def store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")

def test_list_recent_returns_most_recent_first(store):
    now = datetime.now()
    t1 = Task(goal="first task",  created_at=now - timedelta(seconds=1))
    t2 = Task(goal="second task", created_at=now)
    store.save(t1)
    store.save(t2)
    results = store.list_recent(limit=10)
    assert results[0].goal == "second task"
    assert results[1].goal == "first task"

def test_list_recent_respects_limit(store):
    for i in range(5):
        store.save(Task(goal=f"task {i}"))
    results = store.list_recent(limit=3)
    assert len(results) == 3

def test_list_recent_includes_completed(store):
    t = Task(goal="done task")
    store.save(t)
    store.update_status(t.id, TaskStatus.COMPLETED, result="ok")
    results = store.list_recent(limit=10)
    assert any(r.goal == "done task" for r in results)

def test_list_recent_excludes_nightly_sentinel(store):
    t = Task(goal="__nightly_consolidation__")
    store.save(t)
    results = store.list_recent(limit=10)
    assert not any(r.goal == "__nightly_consolidation__" for r in results)

class SimpleMonitor(MonitorDaemon):
    name = "simple"
    async def run(self): pass

@pytest.fixture
def mock_bus():
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus

@pytest.mark.asyncio
async def test_submit_task_sets_last_trigger(mock_bus):
    m = SimpleMonitor(mock_bus)
    assert m._last_trigger is None
    await m.submit_task("do something")
    assert m._last_trigger is not None

def test_monitor_registry_status(mock_bus):
    registry = MonitorRegistry()
    m = SimpleMonitor(mock_bus)
    registry.register(m)
    status = registry.status()
    assert len(status) == 1
    assert status[0]["name"] == "simple"
    assert status[0]["last_trigger"] is None

@pytest.fixture
def ep_store(tmp_path) -> EpisodeStore:
    return EpisodeStore(tmp_path / "ep.db")

def test_delete_episode(ep_store):
    ep = Episode(content="to delete", type="conversation")
    ep_store.record(ep)
    assert len(ep_store.query_recent(limit=10)) == 1
    ep_store.delete(ep.id)
    assert len(ep_store.query_recent(limit=10)) == 0

def test_delete_missing_raises(ep_store):
    with pytest.raises(KeyError):
        ep_store.delete("nonexistent-id")
