import pytest

from pathlib import Path
from datetime import datetime

from app.core.task_store import Task, TaskStatus, TaskStore

@pytest.fixture
def store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "test_tasks.db")

def test_save_and_get_roundtrip(store):
    task = Task(goal="write a report")
    store.save(task)
    retrieved = store.get(task.id)
    assert retrieved is not None
    assert retrieved.goal == "write a report"
    assert retrieved.status == TaskStatus.PENDING
    assert retrieved.is_reversible is True
    assert retrieved.requires_network is False

def test_get_returns_none_for_missing_id(store):
    assert store.get("nonexistent-id-123") is None

def test_update_status_changes_status(store):
    task = Task(goal="test task")
    store.save(task)
    store.update_status(task.id, TaskStatus.IN_PROGRESS)
    updated = store.get(task.id)
    assert updated.status == TaskStatus.IN_PROGRESS

def test_update_status_sets_completed_at_on_completion(store):
    task = Task(goal="complete me")
    store.save(task)
    store.update_status(task.id, TaskStatus.COMPLETED, result="done")
    updated = store.get(task.id)
    assert updated.status == TaskStatus.COMPLETED
    assert updated.result == "done"
    assert updated.completed_at is not None

def test_update_status_sets_completed_at_for_terminal_states(store):
    task = Task(goal="fail me")
    store.save(task)
    store.update_status(task.id, TaskStatus.FAILED)
    updated = store.get(task.id)
    assert updated.completed_at is not None

def test_get_children_returns_subtasks(store):
    parent = Task(goal="parent task")
    child1 = Task(goal="child one", parent_id=parent.id, created_by="planner")
    child2 = Task(goal="child two", parent_id=parent.id, created_by="planner")
    store.save(parent)
    store.save(child1)
    store.save(child2)

    children = store.get_children(parent.id)
    assert len(children) == 2
    goals = {c.goal for c in children}
    assert goals == {"child one", "child two"}

def test_list_active_returns_only_active_tasks(store):
    active = Task(goal="active", status=TaskStatus.IN_PROGRESS)
    done = Task(goal="done", status=TaskStatus.COMPLETED)
    pending = Task(goal="pending")
    store.save(active)
    store.save(done)
    store.save(pending)

    result = store.list_active()
    goals = {t.goal for t in result}
    assert "active" in goals
    assert "pending" in goals
    assert "done" not in goals

def test_save_overwrites_existing_task(store):
    task = Task(goal="original")
    store.save(task)
    task.goal = "updated"
    store.save(task)
    retrieved = store.get(task.id)
    assert retrieved.goal == "updated"

def test_update_status_raises_for_missing_id(store):
    with pytest.raises(KeyError):
        store.update_status("nonexistent-id", TaskStatus.COMPLETED)

def test_new_terminal_statuses_exist():
    assert TaskStatus.INVALID_PLAN.value       == "invalid_plan"
    assert TaskStatus.GOAL_NOT_SATISFIED.value == "goal_not_satisfied"
    assert TaskStatus.TOOL_MISMATCH.value      == "tool_mismatch"

def test_task_expected_outcome_defaults_none():
    task = Task(goal="do something")
    assert task.expected_outcome is None

def test_task_expected_outcome_set():
    task = Task(goal="search web", expected_outcome="List of URLs returned")
    assert task.expected_outcome == "List of URLs returned"

def test_task_depends_on_accepts_string_ids():
    t1 = Task(goal="step one")
    t2 = Task(goal="step two", depends_on=[t1.id])
    assert t1.id in t2.depends_on

def test_new_terminal_statuses_set_completed_at(store):
    for status in (TaskStatus.INVALID_PLAN, TaskStatus.GOAL_NOT_SATISFIED, TaskStatus.TOOL_MISMATCH):
        task = Task(goal=f"test {status.value}")
        store.save(task)
        store.update_status(task.id, status)
        updated = store.get(task.id)
        assert updated.status == status
        assert updated.completed_at is not None, f"completed_at not set for {status}"