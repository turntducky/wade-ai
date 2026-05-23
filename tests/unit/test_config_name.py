from unittest.mock import patch

from app.core.config import ConfigManager

def test_get_assistant_name_defaults_to_wade():
    with patch.object(ConfigManager, "get", return_value={}):
        assert ConfigManager.get_assistant_name() == "W.A.D.E."

def test_get_assistant_name_returns_configured_value():
    with patch.object(ConfigManager, "get", return_value={"assistant_name": "Jarvis"}):
        assert ConfigManager.get_assistant_name() == "Jarvis"

def test_get_assistant_name_falls_back_on_empty_string():
    with patch.object(ConfigManager, "get", return_value={"assistant_name": ""}):
        assert ConfigManager.get_assistant_name() == "W.A.D.E."
