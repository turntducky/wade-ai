from pathlib import Path
from unittest.mock import MagicMock, patch

def test_personality_manager_substitutes_assistant_name(tmp_path):
    identity = tmp_path / "IDENTITY.md"
    identity.write_text("You are {ASSISTANT_NAME} — the presence.", encoding="utf-8")

    with patch("app.core.config.ConfigManager.get_assistant_name", return_value="Jarvis"):
        from app.core.personality import PersonalityManager
        pm = PersonalityManager(chroma_client=None, workspace_dir=tmp_path)
        content = pm._read_from_disk("IDENTITY.md")

    assert "Jarvis" in content
    assert "{ASSISTANT_NAME}" not in content

def test_tier_personality_read_file_substitutes_name(tmp_path):
    identity = tmp_path / "IDENTITY.md"
    identity.write_text("{ASSISTANT_NAME} — an AI assistant.", encoding="utf-8")

    with patch("app.core.config.ConfigManager.get_assistant_name", return_value="Atlas"):
        from app.core.tier_personality import _read_file
        content = _read_file(tmp_path, "IDENTITY.md")

    assert "Atlas" in content
    assert "{ASSISTANT_NAME}" not in content

def test_tier_personality_fallback_uses_config_name(tmp_path):
    with patch("app.core.config.ConfigManager.get_assistant_name", return_value="Nova"):
        from app.core.tier_personality import build_tier_system_prompt
        tier_ctx = MagicMock()
        tier_ctx.is_admin = False
        tier_ctx.tier = "friends"
        tier_ctx.workspace_dir = tmp_path
        result = build_tier_system_prompt("hello", tier_ctx)

    assert result is not None
    assert "Nova" in result
    assert "{ASSISTANT_NAME}" not in result