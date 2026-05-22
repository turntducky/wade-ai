import pytest

from contextlib import asynccontextmanager
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

@asynccontextmanager
async def _noop_lifespan(app):
    """Skip all infrastructure startup for unit tests."""
    yield

@pytest.fixture
def client():
    from app.main import app
    with patch.object(app.router, "lifespan_context", _noop_lifespan):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

def test_ready_returns_false_and_no_error_initially(client):
    import app.main as main
    main._skills_ready.clear()
    main._skills_error = None
    resp = client.get("/api/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["error"] is None

def test_ready_returns_true_after_event_set(client):
    import app.main as main
    main._skills_ready.set()
    main._skills_error = None
    try:
        resp = client.get("/api/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["error"] is None
    finally:
        main._skills_ready.clear()

def test_ready_returns_error_string_when_preload_failed(client):
    import app.main as main
    main._skills_ready.clear()
    main._skills_error = "ChromaDB unavailable"
    try:
        resp = client.get("/api/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is False
        assert data["error"] == "ChromaDB unavailable"
    finally:
        main._skills_error = None


async def test_preload_skills_sets_ready_on_success():
    import app.main as main
    main._skills_ready.clear()
    main._skills_error = None

    mock_router = MagicMock()
    mock_router.index_tools.return_value = None

    try:
        with patch("app.main.load_all_skills", return_value=None), \
             patch("app.main._get_preload_router", return_value=mock_router):
            await main._preload_skills()

        assert main._skills_ready.is_set()
        assert main._skills_error is None
    finally:
        main._skills_ready.clear()

async def test_preload_skills_sets_error_and_leaves_event_unset_on_load_failure():
    import app.main as main
    main._skills_ready.clear()
    main._skills_error = None

    try:
        with patch("app.main.load_all_skills", side_effect=RuntimeError("import failed")):
            await main._preload_skills()

        assert not main._skills_ready.is_set()
        assert main._skills_error is not None
        assert "import failed" in main._skills_error
    finally:
        main._skills_error = None

async def test_preload_skills_sets_error_and_leaves_event_unset_on_index_failure():
    import app.main as main
    main._skills_ready.clear()
    main._skills_error = None

    mock_router = MagicMock()
    mock_router.index_tools.side_effect = RuntimeError("chroma error")

    try:
        with patch("app.main.load_all_skills", return_value=None), \
             patch("app.main._get_preload_router", return_value=mock_router):
            await main._preload_skills()

        assert not main._skills_ready.is_set()
        assert main._skills_error is not None
        assert "chroma error" in main._skills_error
    finally:
        main._skills_error = None