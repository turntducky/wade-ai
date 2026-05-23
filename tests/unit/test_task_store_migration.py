import pytest
import sqlite3

from pathlib import Path
from app.core.task_store import TaskStore, Task

@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")

def test_depends_on_persisted(store):
    t = Task(goal="test", depends_on=["uuid-a", "uuid-b"])
    store.save(t)
    loaded = store.get(t.id)
    assert loaded is not None
    assert loaded.depends_on == ["uuid-a", "uuid-b"]

def test_expected_outcome_persisted(store):
    t = Task(goal="test", expected_outcome="Returns a sorted list")
    store.save(t)
    loaded = store.get(t.id)
    assert loaded is not None
    assert loaded.expected_outcome == "Returns a sorted list"

def test_depends_on_defaults_to_empty_list(store):
    t = Task(goal="simple task")
    store.save(t)
    loaded = store.get(t.id)
    assert loaded.depends_on == []

def test_expected_outcome_defaults_to_none(store):
    t = Task(goal="simple task")
    store.save(t)
    loaded = store.get(t.id)
    assert loaded.expected_outcome is None

def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "tasks.db"
    TaskStore(db)
    TaskStore(db)

def test_existing_db_without_new_columns_is_migrated(tmp_path):
    db = tmp_path / "tasks.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL,
                created_by TEXT NOT NULL, parent_id TEXT,
                requires_network INTEGER NOT NULL DEFAULT 0,
                is_reversible INTEGER NOT NULL DEFAULT 1,
                result TEXT, created_at TEXT NOT NULL, completed_at TEXT
            )
        """)
    store = TaskStore(db)
    t = Task(goal="migrated task", depends_on=["x"])
    store.save(t)
    loaded = store.get(t.id)
    assert loaded is not None
    assert loaded.depends_on == ["x"]