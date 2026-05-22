import pytest

from unittest.mock import MagicMock, patch

from app.services.model_manager import fit_and_install_models, _infer_model_family

@pytest.mark.asyncio
async def test_fit_and_install_models_handles_strings():
    """Test that fit_and_install_models handles suite discovery and installation."""
    mock_suite = {"chat": "qwen2.5:3b", "coding": "qwen2.5-coder:3b"}
    
    with patch("app.services.model_manager.async_generate_optimal_suite", return_value=mock_suite), \
         patch("app.services.model_manager.pull_optimal_models", side_effect=lambda x: x) as MockPull, \
         patch("app.services.model_manager.ConfigManager") as MockConfigManager:
        
        MockConfigManager.get.return_value = {}
        await fit_and_install_models()
        
        MockPull.assert_called_once_with(mock_suite)
        MockConfigManager.save.assert_called_once()

def test_infer_model_family_robustness():
    """Test _infer_model_family with both dict and string inputs."""
    assert _infer_model_family({"chat": "MaziyarPanahi/Phi-3.5-mini-instruct-GGUF"}) == "phi"
    assert _infer_model_family({"chat": {"repo": "unsloth/Mistral-7B-v0.3", "filename": "mistral.gguf"}}) == "mistral"
    assert _infer_model_family({"chat": {"repo": "qwen/Qwen2.5-7B"}}) == "qwen"
    assert _infer_model_family({"chat": "unknown/model"}) == "default"