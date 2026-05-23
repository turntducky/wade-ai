import pytest
import sqlite3

from pathlib import Path
from dataclasses import dataclass

from app.core.telemetry import TelemetryStore

@dataclass
class _FakeTrace:
    tool_name: str = "search"
    args_summary: str = "q=test"
    result_summary: str = "3 results"
    risk: str = "low"
    exit_status: str = "success"
    duration_ms: int = 42
    was_retried: bool = False

@pytest.fixture
def store(tmp_path):
    return TelemetryStore(tmp_path / "telemetry.db")

def test_save_and_get_traces(store):
    traces = [_FakeTrace(), _FakeTrace(tool_name="write", exit_status="error")]
    store.save_traces(traces, task_id="task-abc")
    rows = store.get_traces("task-abc")
    assert len(rows) == 2
    assert rows[0]["tool_name"] == "search"
    assert rows[1]["tool_name"] == "write"
    assert rows[1]["exit_status"] == "error"

def test_get_traces_empty(store):
    assert store.get_traces("nonexistent") == []

def test_save_and_get_verdicts(store):
    store.save_verdict(
        task_id="task-xyz",
        check_type="step",
        status="ok",
        confidence=0.91,
        reason="Looks good",
        surface_to_user=False,
        goal_progress_delta=0.2,
        step_task_id="sub-1",
    )
    rows = store.get_verdicts("task-xyz")
    assert len(rows) == 1
    v = rows[0]
    assert v["check_type"] == "step"
    assert v["confidence"] == pytest.approx(0.91)
    assert v["step_task_id"] == "sub-1"

def test_get_verdicts_empty(store):
    assert store.get_verdicts("nope") == []

def test_record_and_get_metrics(store):
    store.record_metric("chat", "llama3:8b", 100, 50, 430)
    store.record_metric("tools", "qwen2.5:7b", 80, 20, 120)
    rows = store.get_recent_metrics(limit=10)
    assert len(rows) == 2
    assert rows[0]["role"] in ("chat", "tools")

def test_get_recent_metrics_limit(store):
    for i in range(5):
        store.record_metric("fast", "phi3:mini", i * 10, i * 5, i * 100)
    rows = store.get_recent_metrics(limit=3)
    assert len(rows) == 3

def test_tables_are_created_idempotently(tmp_path):
    db = tmp_path / "t.db"
    TelemetryStore(db)
    TelemetryStore(db)