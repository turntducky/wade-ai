import pytest

from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_ensure_running_spawns_ollama_when_not_running():
    """ensure_running() must spawn 'ollama serve' when Ollama is unreachable."""
    from app.services.ollama_manager import OllamaManager
    manager = OllamaManager()
    manager.is_running = AsyncMock(side_effect=[False, True])  # not running, then becomes ready

    hw_mock = {"primary": {"backend": "cpu", "memory_usable_gb": 0.0}}
    with patch("app.core.hardware.probe_hardware", return_value=hw_mock):
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await manager.ensure_running()
            mock_popen.assert_called_once()

@pytest.mark.asyncio
async def test_ensure_model_pulled_calls_ollama_pull():
    """ensure_model_pulled() must run 'ollama pull <model>' if model is not present."""
    from app.services.ollama_manager import OllamaManager
    manager = OllamaManager()
    manager.model_exists = AsyncMock(return_value=False)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        await manager.ensure_model_pulled("llama3.2:3b")
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "pull" in call_args

@pytest.mark.asyncio
async def test_ensure_model_pulled_skips_if_already_present():
    """ensure_model_pulled() must not call ollama pull if model already exists."""
    from app.services.ollama_manager import OllamaManager
    manager = OllamaManager()
    manager.model_exists = AsyncMock(return_value=True)

    with patch("subprocess.run") as mock_run:
        await manager.ensure_model_pulled("llama3.2:3b")
        mock_run.assert_not_called()

@pytest.mark.asyncio
async def test_ensure_running_raises_clear_error_on_missing_binary():
    """ensure_running() must raise RuntimeError with ollama.com URL when binary not found."""
    from app.services.ollama_manager import OllamaManager
    manager = OllamaManager()
    manager.is_running = AsyncMock(return_value=False)

    with patch("subprocess.Popen", side_effect=FileNotFoundError):
        with pytest.raises(RuntimeError, match="ollama.com"):
            await manager.ensure_running()

@pytest.mark.asyncio
async def test_restart_calls_shutdown_then_ensure_running():
    """restart() must stop and restart Ollama."""
    from app.services.ollama_manager import OllamaManager
    manager = OllamaManager()
    manager._we_started_it = True
    manager.shutdown = AsyncMock()
    manager.ensure_running = AsyncMock()

    await manager.restart()

    manager.shutdown.assert_called_once()
    manager.ensure_running.assert_called_once()