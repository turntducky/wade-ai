import json
import pytest
import aiohttp

from unittest.mock import MagicMock, patch, AsyncMock

from app.services.inference_client import InferenceClient
from app.services.model_router import ModelRouter, ModelRoute

@pytest.fixture
def mock_router():
    router = MagicMock(spec=ModelRouter)
    return router

@pytest.fixture
def client(mock_router):
    return InferenceClient(router=mock_router)

@pytest.mark.asyncio
async def test_chat_openai(client, mock_router):
    mock_router.resolve.return_value = ModelRoute(provider="openai", model="gpt-4o")
    
    mock_response = {
        "choices": [{
            "message": {"content": "Hello from OpenAI", "tool_calls": []}
        }]
    }

    with patch("app.core.credentials.CredentialsManager.get", return_value={"api_key": "sk-test"}), \
         patch("app.services.inference_client._get_session") as mock_get_session:
        
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        
        mock_resp_obj = AsyncMock()
        mock_resp_obj.status = 200
        mock_resp_obj.json.return_value = mock_response
        
        mock_session.post.return_value.__aenter__.return_value = mock_resp_obj

        text, tools = await client.chat("chat", [{"role": "user", "content": "hi"}])
        
        assert text == "Hello from OpenAI"
        assert tools == []
        mock_session.post.assert_called_once()
        args, kwargs = mock_session.post.call_args
        assert "api.openai.com" in args[0]
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"

@pytest.mark.asyncio
async def test_chat_gemini(client, mock_router):
    mock_router.resolve.return_value = ModelRoute(provider="gemini", model="gemini-1.5-flash")
    
    mock_response = {
        "candidates": [{
            "content": {"parts": [{"text": "Hello from Gemini"}]}
        }]
    }

    with patch("app.core.credentials.CredentialsManager.get", return_value={"api_key": "gemini-test"}), \
         patch("app.services.inference_client._get_session") as mock_get_session:
        
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        
        mock_resp_obj = AsyncMock()
        mock_resp_obj.status = 200
        mock_resp_obj.json.return_value = mock_response
        
        mock_session.post.return_value.__aenter__.return_value = mock_resp_obj

        text, tools = await client.chat("chat", [{"role": "user", "content": "hi"}])
        
        assert text == "Hello from Gemini"
        assert "generativelanguage.googleapis.com" in mock_session.post.call_args[0][0]
        assert "key=gemini-test" in mock_session.post.call_args[0][0]

@pytest.mark.asyncio
async def test_chat_anthropic(client, mock_router):
    mock_router.resolve.return_value = ModelRoute(provider="anthropic", model="claude-3-haiku")
    
    mock_response = {
        "content": [{"type": "text", "text": "Hello from Claude"}]
    }

    with patch("app.core.credentials.CredentialsManager.get", return_value={"api_key": "ant-test"}), \
         patch("app.services.inference_client._get_session") as mock_get_session:
        
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        
        mock_resp_obj = AsyncMock()
        mock_resp_obj.status = 200
        mock_resp_obj.json.return_value = mock_response
        
        mock_session.post.return_value.__aenter__.return_value = mock_resp_obj

        text, tools = await client.chat("chat", [{"role": "user", "content": "hi"}])
        
        assert text == "Hello from Claude"
        assert "api.anthropic.com" in mock_session.post.call_args[0][0]
        assert mock_session.post.call_args[1]["headers"]["x-api-key"] == "ant-test"

@pytest.mark.asyncio
async def test_embed_openai(client, mock_router):
    mock_router.resolve.return_value = ModelRoute(provider="openai", model="text-embedding-3-small")
    
    mock_response = {
        "data": [{"embedding": [0.1, 0.2, 0.3]}]
    }

    with patch("app.core.credentials.CredentialsManager.get", return_value={"api_key": "sk-test"}), \
         patch("app.services.inference_client._get_session") as mock_get_session:
        
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        
        mock_resp_obj = AsyncMock()
        mock_resp_obj.status = 200
        mock_resp_obj.json.return_value = mock_response
        
        mock_session.post.return_value.__aenter__.return_value = mock_resp_obj

        vec = await client.embed("test")
        
        assert vec == [0.1, 0.2, 0.3]
        assert "embeddings" in mock_session.post.call_args[0][0]

@pytest.mark.asyncio
async def test_is_available_openai(client, mock_router):
    mock_router.resolve.return_value = ModelRoute(provider="openai", model="gpt-4o")
    with patch("app.core.credentials.CredentialsManager.get", return_value={"api_key": "sk-test"}):
        assert await client.is_available() is True
    
    with patch("app.core.credentials.CredentialsManager.get", return_value={}):
        assert await client.is_available() is False

@pytest.mark.asyncio
async def test_is_available_ollama(client, mock_router):
    mock_router.resolve.return_value = ModelRoute(provider="ollama", model="qwen2.5")
    with patch("app.services.inference_client._get_session") as mock_get_session:
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        
        assert await client.is_available() is True