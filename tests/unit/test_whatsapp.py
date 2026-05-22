import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v1.whatsapp import (
    process_and_reply,
    process_voice_and_reply,
)

async def async_generator_mock(*args, **kwargs):
    """Helper to create a proper async generator"""
    yield "Hello "
    yield "from W.A.D.E."

@pytest.mark.asyncio
async def test_process_and_reply_uses_orchestrator():
    """Verify process_and_reply calls orchestrator.process()"""
    with patch("app.api.v1.whatsapp.orchestrator") as mock_orchestrator, \
         patch("app.api.v1.whatsapp.user_registry") as mock_registry, \
         patch("app.api.v1.whatsapp.send_whatsapp_message", new_callable=AsyncMock) as mock_send:

        mock_tier_ctx = MagicMock()
        mock_tier_ctx.session_id_for.return_value = "session_123"
        mock_registry.resolve.return_value = mock_tier_ctx
        mock_orchestrator.process = MagicMock(side_effect=async_generator_mock)

        await process_and_reply("user_001", "Hello assistant")

        mock_orchestrator.process.assert_called_once()
        call_args = mock_orchestrator.process.call_args
        assert call_args[0][0] == "Hello assistant"
        assert call_args[1]["session_id"] == "session_123"

        mock_send.assert_called_once()
        assert "Hello from W.A.D.E." in mock_send.call_args[1]["message"]

async def async_generator_voice_mock(*args, **kwargs):
    """Helper to create a proper async generator for voice"""
    yield "I heard you "
    yield "say hello"

@pytest.mark.asyncio
async def test_process_voice_and_reply_uses_orchestrator():
    """Verify process_voice_and_reply calls orchestrator.process()"""
    with patch("app.api.v1.whatsapp.orchestrator") as mock_orchestrator, \
         patch("app.api.v1.whatsapp.user_registry") as mock_registry, \
         patch("app.api.v1.whatsapp.get_voice_service") as mock_voice_svc, \
         patch("app.api.v1.whatsapp.tempfile") as mock_tempfile, \
         patch("app.api.v1.whatsapp.base64") as mock_b64, \
         patch("app.api.v1.whatsapp.os") as mock_os, \
         patch("app.api.v1.whatsapp.asyncio") as mock_asyncio, \
         patch("app.api.v1.whatsapp.httpx.AsyncClient") as mock_client:

        mock_tier_ctx = MagicMock()
        mock_tier_ctx.session_id_for.return_value = "session_456"
        mock_registry.resolve.return_value = mock_tier_ctx

        mock_voice = MagicMock()
        mock_voice.transcribe_file = MagicMock(return_value="user said hello")
        mock_voice.generate_audio_file = AsyncMock()
        mock_voice_svc.return_value = mock_voice

        mock_tempfile.mkstemp.side_effect = [(10, "/tmp/in.ogg"), (11, "/tmp/out.ogg")]
        mock_os.fdopen = MagicMock()
        mock_os.close = MagicMock()
        mock_os.path.exists.return_value = True
        mock_os.remove = MagicMock()

        mock_b64.b64decode.return_value = b"audio_data"
        mock_b64.b64encode.return_value = b"encoded_audio"

        mock_orchestrator.process = MagicMock(side_effect=async_generator_voice_mock)

        mock_asyncio.to_thread = AsyncMock()
        mock_asyncio.to_thread.side_effect = [
            "user said hello",
            None,
        ]

        mock_http_client = AsyncMock()
        mock_client.return_value.__aenter__.return_value = mock_http_client

        await process_voice_and_reply("user_001", "base64_encoded_audio_data")

        mock_orchestrator.process.assert_called_once()
        call_args = mock_orchestrator.process.call_args
        assert "user said hello" in call_args[0][0]
        assert call_args[1]["session_id"] == "session_456"