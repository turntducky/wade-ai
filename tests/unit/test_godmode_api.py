import pytest

from contextlib import asynccontextmanager
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

@asynccontextmanager
async def _noop_lifespan(app):
    """Minimal lifespan that skips all infrastructure startup for unit tests."""
    yield

def _allow_admin():
    """Dependency override: always allow admin access."""
    from app.core.user_registry import TierContext
    return TierContext.admin()

@pytest.fixture
def client():
    from app.main import app
    from app.core.security import require_admin

    with patch.object(app.router, "lifespan_context", _noop_lifespan):
        app.dependency_overrides[require_admin] = _allow_admin
        try:
            with TestClient(app, raise_server_exceptions=True) as c:
                yield c
        finally:
            app.dependency_overrides.pop(require_admin, None)

def _csrf_token(client: TestClient) -> str:
    """Retrieve the session CSRF token from the app."""
    return client.get("/api/csrf-token").json()["token"]

def test_traces_returns_404_for_unknown_task(client):
    resp = client.get(
        "/api/godmode/traces/nonexistent-id",
        headers={"X-Device-ID": "test-device"},
    )
    assert resp.status_code == 404

def test_metrics_live_returns_dict(client):
    resp = client.get(
        "/api/godmode/metrics/live",
        headers={"X-Device-ID": "test-device"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "by_role" in data
    assert "recent" in data
    assert "totals" in data

def test_replay_returns_404_for_unknown_task(client):
    token = _csrf_token(client)
    resp = client.post(
        "/api/godmode/tasks/nonexistent-id/replay",
        headers={"X-Device-ID": "test-device", "X-WADE-Token": token},
    )
    assert resp.status_code == 404

def test_traces_response_shape_for_known_task(client, tmp_path):
    from app.core.task_store import Task, TaskStore
    from app.core.orchestrator import orchestrator

    store = TaskStore(tmp_path / "tasks.db")
    t = Task(goal="test goal", created_by="test")
    store.save(t)

    original_store = orchestrator._store
    orchestrator._store = store
    try:
        resp = client.get(
            f"/api/godmode/traces/{t.id}",
            headers={"X-Device-ID": "test-device"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task"]["id"] == t.id
        assert data["task"]["goal"] == "test goal"
        assert "subtasks" in data
        assert "root_verdicts" in data
    finally:
        orchestrator._store = original_store