import pytest
import asyncio

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.telemetry import TelemetryStore
from app.services.model_router import ModelRoute
import app.services.inference_client as ic_module
from app.core.task_store import Task, TaskStore, TaskStatus

@pytest.fixture(autouse=True)
def reset_hook():
    ic_module._metrics_hook = None
    yield
    ic_module._metrics_hook = None

@pytest.fixture
def telemetry(tmp_path):
    return TelemetryStore(tmp_path / "tel.db")

@pytest.fixture
def task_store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")

@pytest.mark.asyncio
async def test_metrics_hook_writes_to_telemetry(telemetry):
    """Wiring the hook to TelemetryStore.record_metric should produce a DB row."""
    async def hook(role, model, pt, ct, lat):
        await asyncio.to_thread(telemetry.record_metric, role, model, pt, ct, lat)

    ic_module.set_metrics_hook(hook)

    fake_data = {
        "message": {"content": "test", "tool_calls": []},
        "prompt_eval_count": 20,
        "eval_count": 10,
        "eval_duration": 300_000_000,
    }
    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=fake_data)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)

    from app.services.inference_client import InferenceClient
    client = InferenceClient()

    with patch("app.services.inference_client._get_session", return_value=mock_session):
        with patch.object(client._router, "resolve", return_value=ModelRoute(provider="ollama", model="llama3:8b")):
            await client.chat("chat", [{"role": "user", "content": "hi"}])

    rows = telemetry.get_recent_metrics(limit=10)
    assert len(rows) == 1
    assert rows[0]["role"] == "chat"
    assert rows[0]["completion_tokens"] == 10
    assert rows[0]["latency_ms"] == 300

def test_task_depends_on_survives_round_trip(task_store):
    """Task.depends_on must survive save → get."""
    dep_id = "some-uuid"
    t = Task(goal="child task", depends_on=[dep_id])
    task_store.save(t)
    loaded = task_store.get(t.id)
    assert loaded.depends_on == [dep_id]

def test_telemetry_save_and_retrieve_traces(telemetry):
    from dataclasses import dataclass

    @dataclass
    class FakeTrace:
        tool_name: str = "run_code"
        args_summary: str = "print('hello')"
        result_summary: str = "hello"
        risk: str = "low"
        exit_status: str = "success"
        duration_ms: int = 55
        was_retried: bool = False

    telemetry.save_traces([FakeTrace()], "task-999")
    rows = telemetry.get_traces("task-999")
    assert rows[0]["tool_name"] == "run_code"
    assert rows[0]["duration_ms"] == 55