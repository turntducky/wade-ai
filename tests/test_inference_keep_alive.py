from unittest.mock import AsyncMock, MagicMock, patch

class AsyncIterator:
    """Helper to make an async iterator from sync data."""
    def __init__(self, data):
        self.data = iter(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.data)
        except StopIteration:
            raise StopAsyncIteration

async def test_complete_payload_includes_keep_alive():
    """complete() must send keep_alive=-1 in every Ollama request payload."""
    from app.services.inference_client import InferenceClient

    captured = {}

    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.content = AsyncIterator([
        b'{"message": {"content": "hello"}, "done": false}\n',
        b'{"done": true, "eval_count": 1, "prompt_eval_count": 1, "eval_duration": 1000000}\n',
    ])

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)

    def fake_post(url, json=None, timeout=None):
        """Capture the json payload and return the context manager."""
        captured.update(json or {})
        return ctx

    mock_session = MagicMock()
    mock_session.post = fake_post

    with patch("app.services.inference_client._get_session", return_value=mock_session):
        client = InferenceClient()
        chunks = [c async for c in client.complete("chat", [{"role": "user", "content": "hi"}])]

    assert "keep_alive" in captured, f"keep_alive not in payload: {captured.keys()}"
    assert captured["keep_alive"] == -1

async def test_chat_payload_includes_keep_alive():
    """chat() must send keep_alive=-1 in every Ollama request payload."""
    from app.services.inference_client import InferenceClient

    captured = {}

    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value={
        "message": {"content": "hello", "tool_calls": []},
        "eval_count": 1,
        "prompt_eval_count": 1,
        "eval_duration": 1000000,
    })

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)

    def fake_post(url, json=None, timeout=None):
        """Capture the json payload and return the context manager."""
        captured.update(json or {})
        return ctx

    mock_session = MagicMock()
    mock_session.post = fake_post

    with patch("app.services.inference_client._get_session", return_value=mock_session):
        client = InferenceClient()
        text, tool_calls = await client.chat("chat", [{"role": "user", "content": "hi"}])

    assert "keep_alive" in captured, f"keep_alive not in payload: {captured.keys()}"
    assert captured["keep_alive"] == -1