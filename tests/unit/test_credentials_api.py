import pytest
import httpx

from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

from app.api.v1.credentials import router, SERVICE_REGISTRY

_app = FastAPI()
_app.include_router(router)
client = TestClient(_app, raise_server_exceptions=False)

@pytest.fixture(autouse=True)
def clear_creds():
    """Isolate each test by patching CredentialsManager."""
    with patch("app.api.v1.credentials.CredentialsManager") as mock_cm:
        mock_cm.get.return_value = None
        mock_cm.save.return_value = None
        yield mock_cm

def test_get_credentials_returns_all_services(clear_creds):
    clear_creds.get.return_value = {"api_key": "sk-test"}
    r = client.get("/api/credentials")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == set(SERVICE_REGISTRY.keys())
    assert data["openai"]["configured"] is True
    assert "api_key" in data["openai"]["fields"]

def test_get_credentials_not_configured_when_empty(clear_creds):
    clear_creds.get.return_value = {}
    r = client.get("/api/credentials")
    assert r.json()["openai"]["configured"] is False

def test_post_saves_credentials(clear_creds):
    r = client.post("/api/credentials/openai", json={"api_key": "sk-abc"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    clear_creds.save.assert_called_once_with("openai", {"api_key": "sk-abc"})

def test_post_unknown_service_returns_404(clear_creds):
    r = client.post("/api/credentials/notaservice", json={"api_key": "x"})
    assert r.status_code == 404

def test_delete_clears_credentials(clear_creds):
    r = client.delete("/api/credentials/anthropic")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    clear_creds.save.assert_called_once_with("anthropic", {})

def test_delete_unknown_service_returns_404(clear_creds):
    r = client.delete("/api/credentials/notaservice")
    assert r.status_code == 404

class _AsyncCtx:
    """Supports both `await c.get(...)` and `async with c.get(...) as r:`."""
    def __init__(self, obj):
        self._obj = obj

    def __await__(self):
        async def _coro():
            return self._obj
        return _coro().__await__()

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, *_):
        pass

def _async_ctx(obj):
    return _AsyncCtx(obj)

@pytest.mark.asyncio
async def test_test_openai_verified(clear_creds):
    clear_creds.get.return_value = {"api_key": "sk-real"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    with patch("app.api.v1.credentials.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get = MagicMock(return_value=_async_ctx(mock_resp))
        r = client.post("/api/credentials/openai/test")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["message"] == "Verified"

@pytest.mark.asyncio
async def test_test_openai_invalid_key(clear_creds):
    clear_creds.get.return_value = {"api_key": "bad"}
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=MagicMock(status_code=401)
    )

    with patch("app.api.v1.credentials.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get = MagicMock(return_value=_async_ctx(mock_resp))
        r = client.post("/api/credentials/openai/test")

    assert r.status_code == 200
    assert r.json()["ok"] is False

@pytest.mark.asyncio
async def test_test_anthropic_verified(clear_creds):
    clear_creds.get.return_value = {"api_key": "sk-ant-real"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("app.api.v1.credentials.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.post = MagicMock(return_value=_async_ctx(mock_resp))
        r = client.post("/api/credentials/anthropic/test")

    assert r.status_code == 200
    assert r.json()["ok"] is True

@pytest.mark.asyncio
async def test_test_anthropic_unauthorized(clear_creds):
    clear_creds.get.return_value = {"api_key": "bad"}
    mock_resp = MagicMock(status_code=401)

    with patch("app.api.v1.credentials.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.post = MagicMock(return_value=_async_ctx(mock_resp))
        r = client.post("/api/credentials/anthropic/test")

    assert r.json()["ok"] is False
    assert "Invalid" in r.json()["message"]

@pytest.mark.asyncio
async def test_test_gemini_verified(clear_creds):
    clear_creds.get.return_value = {"api_key": "AIza-real"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    with patch("app.api.v1.credentials.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get = MagicMock(return_value=_async_ctx(mock_resp))
        r = client.post("/api/credentials/gemini/test")

    assert r.json()["ok"] is True

def test_test_unknown_service_returns_404(clear_creds):
    r = client.post("/api/credentials/notaservice/test")
    assert r.status_code == 404

def test_test_not_configured_returns_error(clear_creds):
    clear_creds.get.return_value = {}
    r = client.post("/api/credentials/openai/test")
    assert r.json()["ok"] is False
    assert "configured" in r.json()["message"].lower()

@pytest.mark.asyncio
async def test_test_notion_verified(clear_creds):
    clear_creds.get.return_value = {"token": "secret_real"}
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("app.api.v1.credentials.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get = MagicMock(return_value=_async_ctx(mock_resp))
        r = client.post("/api/credentials/notion/test")

    assert r.json()["ok"] is True

@pytest.mark.asyncio
async def test_test_blink_verified(clear_creds):
    clear_creds.get.return_value = {"email": "user@x.com", "password": "pass"}

    with patch("app.api.v1.credentials.Blink") as mock_blink_cls, \
         patch("app.api.v1.credentials.BlinkAuth") as mock_auth_cls, \
         patch("app.api.v1.credentials.asyncio.wait_for", new_callable=AsyncMock):
        r = client.post("/api/credentials/blink/test")

    assert r.json()["ok"] is True

@pytest.mark.asyncio
async def test_test_blink_failure(clear_creds):
    clear_creds.get.return_value = {"email": "bad@x.com", "password": "wrong"}

    with patch("app.api.v1.credentials.Blink"), \
         patch("app.api.v1.credentials.BlinkAuth"), \
         patch("app.api.v1.credentials.asyncio.wait_for",
               side_effect=Exception("Login failed")):
        r = client.post("/api/credentials/blink/test")

    assert r.json()["ok"] is False

@pytest.mark.asyncio
async def test_test_spotify_verified(clear_creds):
    clear_creds.get.return_value = {"client_id": "cid", "client_secret": "csec"}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"access_token": "tok"}

    with patch("app.api.v1.credentials.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.post = MagicMock(return_value=_async_ctx(mock_resp))
        r = client.post("/api/credentials/spotify/test")

    assert r.json()["ok"] is True

@pytest.mark.asyncio
async def test_test_spotify_bad_credentials(clear_creds):
    clear_creds.get.return_value = {"client_id": "bad", "client_secret": "bad"}
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=MagicMock(status_code=401)
    )

    with patch("app.api.v1.credentials.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.post = MagicMock(return_value=_async_ctx(mock_resp))
        r = client.post("/api/credentials/spotify/test")

    assert r.json()["ok"] is False