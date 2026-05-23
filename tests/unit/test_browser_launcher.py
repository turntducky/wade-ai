import sys
import webbrowser

from typing import Any
from unittest.mock import MagicMock, patch

for _mod in ("whisper", "torch", "onnxruntime", "sounddevice",
             "openwakeword", "kokoro_onnx"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import types
if "app.services.voice" not in sys.modules:
    _voice_stub: Any = types.ModuleType("app.services.voice")
    _voice_stub.get_voice_service = MagicMock()
    sys.modules["app.services.voice"] = _voice_stub

from app.core.browser_launcher import open_ui

URL = "http://127.0.0.1:8000/ui"

def test_system_default_when_no_config_no_override():
    with patch("app.core.browser_launcher.ConfigManager") as mock_cm, \
         patch("app.core.browser_launcher.webbrowser.open") as mock_open:
        mock_cm.get.return_value = {}
        open_ui(URL)
    mock_open.assert_called_once_with(URL)

def test_override_takes_precedence_over_config():
    with patch("app.core.browser_launcher.ConfigManager") as mock_cm, \
         patch("app.core.browser_launcher.webbrowser.get") as mock_get:
        mock_cm.get.return_value = {"preferred_browser": "chrome"}
        mock_get.return_value.open.return_value = None
        open_ui(URL, browser_override="firefox")
    mock_get.assert_called_with("firefox")

def test_config_browser_used_when_no_override():
    with patch("app.core.browser_launcher.ConfigManager") as mock_cm, \
         patch("app.core.browser_launcher.webbrowser.get") as mock_get:
        mock_cm.get.return_value = {"preferred_browser": "edge"}
        mock_get.return_value.open.return_value = None
        open_ui(URL)
    mock_get.assert_called_with("microsoft-edge")

def test_chrome_tries_google_chrome_key_first():
    with patch("app.core.browser_launcher.ConfigManager") as mock_cm, \
         patch("app.core.browser_launcher.webbrowser.get") as mock_get:
        mock_cm.get.return_value = {}
        mock_get.return_value.open.return_value = None
        open_ui(URL, browser_override="chrome")
    mock_get.assert_called_with("google-chrome")

def test_falls_back_to_next_key_on_webbrowser_error():
    with patch("app.core.browser_launcher.ConfigManager") as mock_cm, \
         patch("app.core.browser_launcher.webbrowser.get") as mock_get:
        mock_cm.get.return_value = {}
        working_browser = MagicMock()
        mock_get.side_effect = [webbrowser.Error("not found"), working_browser]
        open_ui(URL, browser_override="chrome")
    assert mock_get.call_count == 2
    working_browser.open.assert_called_once_with(URL)

def test_falls_back_to_system_default_when_all_keys_fail(capsys):
    with patch("app.core.browser_launcher.ConfigManager") as mock_cm, \
         patch("app.core.browser_launcher.webbrowser.get") as mock_get, \
         patch("app.core.browser_launcher.webbrowser.open") as mock_open:
        mock_cm.get.return_value = {}
        mock_get.side_effect = webbrowser.Error("not found")
        open_ui(URL, browser_override="firefox")
    mock_open.assert_called_once_with(URL)
    captured = capsys.readouterr()
    assert "firefox" in captured.out

def test_safari_warns_and_falls_back_on_non_macos(capsys):
    with patch("app.core.browser_launcher.ConfigManager") as mock_cm, \
         patch("app.core.browser_launcher.sys") as mock_sys, \
         patch("app.core.browser_launcher.webbrowser.open") as mock_open, \
         patch("app.core.browser_launcher.webbrowser.get") as mock_get:
        mock_cm.get.return_value = {}
        mock_sys.platform = "win32"
        open_ui(URL, browser_override="safari")
    mock_open.assert_called_once_with(URL)
    mock_get.assert_not_called()
    captured = capsys.readouterr()
    assert "Safari" in captured.out

def test_safari_works_on_macos():
    with patch("app.core.browser_launcher.ConfigManager") as mock_cm, \
         patch("app.core.browser_launcher.sys") as mock_sys, \
         patch("app.core.browser_launcher.webbrowser.get") as mock_get:
        mock_cm.get.return_value = {}
        mock_sys.platform = "darwin"
        mock_get.return_value.open.return_value = None
        open_ui(URL, browser_override="safari")
    mock_get.assert_called_with("safari")