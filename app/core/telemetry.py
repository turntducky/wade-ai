from __future__ import annotations

import uuid
import sqlite3
import logging

from typing import Any
from pathlib import Path
from datetime import datetime

from app.core.config import DUCK_HOME

logger = logging.getLogger("wade.telemetry")

TELEMETRY_DB_PATH: Path = DUCK_HOME / "telemetry.db"

_CREATE_TOOL_TRACES = """
CREATE TABLE IF NOT EXISTS tool_traces (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    tool_name      TEXT NOT NULL,
    args_summary   TEXT,
    result_summary TEXT,
    risk           TEXT,
    exit_status    TEXT NOT NULL,
    duration_ms    INTEGER,
    was_retried    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
)
"""

_CREATE_CRITIC_VERDICTS = """
CREATE TABLE IF NOT EXISTS critic_verdicts (
    id                  TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL,
    check_type          TEXT NOT NULL,
    step_task_id        TEXT,
    status              TEXT NOT NULL,
    confidence          REAL NOT NULL,
    reason              TEXT,
    surface_to_user     INTEGER NOT NULL DEFAULT 0,
    goal_progress_delta REAL,
    created_at          TEXT NOT NULL
)
"""

_CREATE_INFERENCE_METRICS = """
CREATE TABLE IF NOT EXISTS inference_metrics (
    id                TEXT PRIMARY KEY,
    role              TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
)
"""

_CREATE_AUDIT_LOGS = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    args        TEXT,
    user_tier   TEXT NOT NULL,
    approved    INTEGER NOT NULL,
    created_at  TEXT NOT NULL
)
"""

class TelemetryStore:
    """Append-only SQLite store for observability data: tool traces, critic verdicts, and inference metrics."""

    def __init__(self, db_path: Path = TELEMETRY_DB_PATH) -> None:
        self._db_path = str(db_path)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(_CREATE_TOOL_TRACES)
            conn.execute(_CREATE_CRITIC_VERDICTS)
            conn.execute(_CREATE_INFERENCE_METRICS)
            conn.execute(_CREATE_AUDIT_LOGS)

    def save_traces(self, traces: list, task_id: str) -> None:
        if not traces:
            return
        now = datetime.now().isoformat()
        rows = [
            (
                str(uuid.uuid4()),
                task_id,
                t.tool_name,
                t.args_summary,
                t.result_summary,
                t.risk,
                t.exit_status,
                t.duration_ms,
                int(t.was_retried),
                now,
            )
            for t in traces
        ]
        with sqlite3.connect(self._db_path) as conn:
            conn.executemany(
                """INSERT INTO tool_traces
                   (id, task_id, tool_name, args_summary, result_summary,
                    risk, exit_status, duration_ms, was_retried, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def get_traces(self, task_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM tool_traces WHERE task_id=? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_verdict(
        self,
        task_id: str,
        check_type: str,
        status: str,
        confidence: float,
        reason: str | None = None,
        surface_to_user: bool = False,
        goal_progress_delta: float | None = None,
        step_task_id: str | None = None,
    ) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO critic_verdicts
                   (id, task_id, check_type, step_task_id, status, confidence,
                    reason, surface_to_user, goal_progress_delta, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    task_id,
                    check_type,
                    step_task_id,
                    status,
                    confidence,
                    reason,
                    int(surface_to_user),
                    goal_progress_delta,
                    datetime.now().isoformat(),
                ),
            )

    def get_verdicts(self, task_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM critic_verdicts WHERE task_id=? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_metric(
        self,
        role: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
    ) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO inference_metrics
                   (id, role, model, prompt_tokens, completion_tokens, latency_ms, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    role,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    latency_ms,
                    datetime.now().isoformat(),
                ),
            )

    def get_recent_metrics(self, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM inference_metrics ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def log_audit(
        self,
        task_id:   str,
        tool_name: str,
        args:      str,
        user_tier: str,
        approved:  bool,
    ) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO audit_logs
                   (id, task_id, tool_name, args, user_tier, approved, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    task_id,
                    tool_name,
                    args,
                    user_tier,
                    int(approved),
                    datetime.now().isoformat(),
                ),
            )

    def prune_old(self, max_age_days: int = 30) -> int:
        """Delete rows older than max_age_days from all telemetry tables."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        total = 0
        with sqlite3.connect(self._db_path) as conn:
            for table in ("tool_traces", "critic_verdicts", "inference_metrics", "audit_logs"):
                cur = conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
                total += cur.rowcount
        return total

    def get_audit_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]