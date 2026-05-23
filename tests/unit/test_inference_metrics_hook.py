import pytest
import asyncio

from app.services.model_router import ModelRoute
import app.services.inference_client as ic_module
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture(autouse=True)
def reset_hook():
    ic_module._metrics_hook = None
    yield
    ic_module._metrics_hook = None

def test_set_metrics_hook_stores_callable():
    async def my_hook(role, model, pt, ct, lat): pass
    ic_module.set_metrics_hook(my_hook)
    assert ic_module._metrics_hook is my_hook

@pytest.mark.asyncio
async def test_chat_calls_hook_with_token_data():
    hook = AsyncMock()
    ic_module.set_metrics_hook(hook)

    fake_response = {
        "message": {"content": "hello", "tool_calls": []},
        "prompt_eval_count": 10,
        "eval_count": 5,
        "eval_duration": 500_000_000,  # 500ms in nanoseconds
    }

    from app.services.inference_client import InferenceClient
    client = InferenceClient()

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=fake_response)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)

    with patch("app.services.inference_client._get_session", return_value=mock_session):
        with patch.object(client._router, "resolve", return_value=ModelRoute(provider="ollama", model="llama3:8b")):
            await client.chat("chat", [{"role": "user", "content": "hi"}])

    hook.assert_awaited_once_with("chat", "llama3:8b", 10, 5, 500)

@pytest.mark.asyncio
async def test_chat_does_not_raise_when_hook_is_none():
    from app.services.inference_client import InferenceClient
    client = InferenceClient()

    fake_response = {
        "message": {"content": "hello", "tool_calls": []},
        "prompt_eval_count": 10,
        "eval_count": 5,
        "eval_duration": 200_000_000,
    }

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=fake_response)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_resp)

    with patch("app.services.inference_client._get_session", return_value=mock_session):
        with patch.object(client._router, "resolve", return_value=ModelRoute(provider="ollama", model="llama3:8b")):
            text, _ = await client.chat("chat", [{"role": "user", "content": "hi"}])
    assert text == "hello"