from __future__ import annotations

import uuid
import json
import sqlite3

from enum import Enum
from typing import Any
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

class TaskStatus(str, Enum):
    PENDING            = "pending"
    PLANNING           = "planning"
    IN_PROGRESS        = "in_progress"
    COMPLETED          = "completed"
    FAILED             = "failed"
    AWAITING_APPROVAL  = "awaiting_approval"
    CANCELLED          = "cancelled"
    INVALID_PLAN       = "invalid_plan"
    GOAL_NOT_SATISFIED = "goal_not_satisfied"
    TOOL_MISMATCH      = "tool_mismatch"

@dataclass
class Task:
    goal:             str
    id:               str             = field(default_factory=lambda: str(uuid.uuid4()))
    status:           TaskStatus      = TaskStatus.PENDING
    created_by:       str             = "user"
    parent_id:        str | None      = None
    requires_network: bool            = False
    is_reversible:    bool            = True
    result:           str | None      = None
    created_at:       datetime        = field(default_factory=datetime.now)
    completed_at:     datetime | None = None
    depends_on:       list[str]       = field(default_factory=list)
    expected_outcome: str | None      = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    goal             TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    created_by       TEXT    NOT NULL,
    parent_id        TEXT,
    requires_network INTEGER NOT NULL DEFAULT 0,
    is_reversible    INTEGER NOT NULL DEFAULT 1,
    result           TEXT,
    created_at       TEXT    NOT NULL,
    completed_at     TEXT,
    depends_on       TEXT,
    expected_outcome TEXT
)
"""

def _migrate_schema(conn: sqlite3.Connection) -> None:
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "depends_on" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN depends_on TEXT")
    if "expected_outcome" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN expected_outcome TEXT")

_ACTIVE_STATUSES = (
    TaskStatus.PENDING.value,
    TaskStatus.PLANNING.value,
    TaskStatus.IN_PROGRESS.value,
    TaskStatus.AWAITING_APPROVAL.value,
)

_TERMINAL_STATUSES = frozenset({
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.INVALID_PLAN,
    TaskStatus.GOAL_NOT_SATISFIED,
    TaskStatus.TOOL_MISMATCH,
})

class TaskStore:
    """Thread-safe SQLite store for Task objects."""
    def __init__(self, db_path: Path) -> None:
        self._db_path = str(db_path)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(_CREATE_SQL)
            _migrate_schema(conn)

    def save(self, task: Task) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tasks
                   (id, goal, status, created_by, parent_id,
                    requires_network, is_reversible, result,
                    created_at, completed_at, depends_on, expected_outcome)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task.id, task.goal, task.status.value, task.created_by,
                    task.parent_id, int(task.requires_network),
                    int(task.is_reversible), task.result,
                    task.created_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                    json.dumps(task.depends_on),
                    task.expected_outcome,
                ),
            )

    def get(self, task_id: str) -> Task | None:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row else None

    def get_children(self, parent_id: str) -> list[Task]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE parent_id=?", (parent_id,)
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def update_status(self, task_id: str, status: TaskStatus, result: str | None = None) -> None:
        completed_at = (
            datetime.now().isoformat() if status in _TERMINAL_STATUSES else None
        )
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE tasks SET status=?, result=?, completed_at=? WHERE id=?",
                (status.value, result, completed_at, task_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"No task with id={task_id!r}")

    def list_active(self) -> list[Task]:
        placeholders = ",".join("?" * len(_ACTIVE_STATUSES))
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status IN ({placeholders})",
                _ACTIVE_STATUSES,
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def list_recent(self, limit: int = 50) -> list[Task]:
        """Return the most recently created tasks, newest first. Excludes internal sentinels."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE goal != ? ORDER BY created_at DESC LIMIT ?",
                ("__nightly_consolidation__", limit),
            ).fetchall()
        return [_row_to_task(r) for r in rows]

def _row_to_task(row: Any) -> Task:
    depends_on_raw = row[10] if len(row) > 10 else None
    return Task(
        id=row[0], goal=row[1], status=TaskStatus(row[2]),
        created_by=row[3], parent_id=row[4],
        requires_network=bool(row[5]), is_reversible=bool(row[6]),
        result=row[7],
        created_at=datetime.fromisoformat(row[8]),
        completed_at=datetime.fromisoformat(row[9]) if row[9] else None,
        depends_on=json.loads(depends_on_raw) if depends_on_raw else [],
        expected_outcome=row[11] if len(row) > 11 else None,
    )