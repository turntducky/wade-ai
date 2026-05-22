import pytest

from unittest.mock import patch
from fastapi.testclient import TestClient
from contextlib import asynccontextmanager

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

def test_delete_memory_returns_success_when_clear_succeeds(client):
    token = _csrf_token(client)
    with patch("app.main.clear_memory", return_value=True) as mock_clear:
        resp = client.delete(
            "/api/memory",
            headers={"X-Device-ID": "test-device", "X-WADE-Token": token},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    mock_clear.assert_called_once()

def test_delete_memory_returns_error_when_clear_fails(client):
    token = _csrf_token(client)
    with patch("app.main.clear_memory", return_value=False):
        resp = client.delete(
            "/api/memory",
            headers={"X-Device-ID": "test-device", "X-WADE-Token": token},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"