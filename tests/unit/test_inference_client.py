import json
import pytest
import aiohttp

from unittest.mock import patch, AsyncMock, MagicMock

from app.services.model_router import ModelRouter
from app.services.inference_client import InferenceClient

def make_client() -> InferenceClient:
    router = ModelRouter({"fast": "qwen2.5:3b", "tools": "qwen2.5:7b", "embeddings": "nomic-embed-text"})
    return InferenceClient(router=router)

@pytest.mark.asyncio
async def test_is_available_true_when_api_responds():
    client = make_client()
    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.status = 200

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_resp)

    with patch("app.services.inference_client.aiohttp.ClientSession", return_value=mock_session):
        assert await client.is_available() is True

@pytest.mark.asyncio
async def test_is_available_false_on_error():
    client = make_client()
    with patch("app.services.inference_client.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value.__aenter__.side_effect = Exception("refused")
        assert await client.is_available() is False

@pytest.mark.asyncio
async def test_complete_streams_content_chunks():
    client = make_client()

    lines = [
        json.dumps({"message": {"content": "Hello"}, "done": False}).encode(),
        json.dumps({"message": {"content": " world"}, "done": True}).encode(),
    ]

    async def fake_content():
        for line in lines:
            yield line

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = fake_content()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_resp)

    with patch("app.services.inference_client.aiohttp.ClientSession", return_value=mock_session):
        chunks = []
        async for chunk in client.complete("fast", [{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

    assert chunks == ["Hello", " world"]

@pytest.mark.asyncio
async def test_chat_returns_text_and_empty_tool_calls():
    client = make_client()

    response_data = {
        "message": {"role": "assistant", "content": "The answer is 42."},
        "done": True,
    }

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=response_data)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_resp)

    with patch("app.services.inference_client.aiohttp.ClientSession", return_value=mock_session):
        text, tool_calls = await client.chat("fast", [{"role": "user", "content": "?"}])

    assert text == "The answer is 42."
    assert tool_calls == []

@pytest.mark.asyncio
async def test_chat_returns_tool_calls_when_present():
    client = make_client()

    tc = [{"function": {"name": "web_search", "arguments": {"query": "test"}}}]
    response_data = {
        "message": {"role": "assistant", "content": "", "tool_calls": tc},
        "done": True,
    }

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=response_data)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_resp)

    with patch("app.services.inference_client.aiohttp.ClientSession", return_value=mock_session):
        text, tool_calls = await client.chat("tools", [{"role": "user", "content": "search"}], tools=[{}])

    assert tool_calls == tc

@pytest.mark.asyncio
async def test_chat_raises_on_connection_error():
    """chat() propagates a connection error when Ollama is unreachable."""
    client = make_client()

    mock_conn_key = MagicMock()
    mock_conn_key.host = "localhost"
    mock_conn_key.port = 11434
    mock_conn_key.ssl = False

    err = aiohttp.ClientConnectorError(
        connection_key=mock_conn_key,
        os_error=OSError("Connection refused"),
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(side_effect=err)

    with patch("app.services.inference_client.aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(aiohttp.ClientConnectorError):
            await client.chat("fast", [{"role": "user", "content": "hello"}])

@pytest.mark.asyncio
async def test_chat_handles_malformed_json_response():
    """chat() raises or surfaces a clear error on a non-JSON / malformed response."""
    import aiohttp as _aiohttp

    client = make_client()

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(
        side_effect=_aiohttp.ContentTypeError(
            request_info=MagicMock(), history=()
        )
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_resp)

    with patch("app.services.inference_client.aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(Exception):
            await client.chat("fast", [{"role": "user", "content": "hello"}])

@pytest.mark.asyncio
async def test_complete_yields_chunks_from_streaming_response():
    """complete() yields individual text chunks from a streaming Ollama response."""
    client = make_client()

    stream_lines = [
        json.dumps({"message": {"content": "Chunk1"}, "done": False}).encode(),
        json.dumps({"message": {"content": "Chunk2"}, "done": False}).encode(),
        json.dumps({"message": {"content": "Chunk3"}, "done": True}).encode(),
    ]

    async def fake_content():
        for line in stream_lines:
            yield line

    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = fake_content()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_resp)

    with patch("app.services.inference_client.aiohttp.ClientSession", return_value=mock_session):
        chunks = []
        async for chunk in client.complete("fast", [{"role": "user", "content": "go"}]):
            chunks.append(chunk)

    assert chunks == ["Chunk1", "Chunk2", "Chunk3"]