import sys
import types
import asyncio

from typing import Any

from unittest.mock import MagicMock, AsyncMock, patch

if "playwright" not in sys.modules:
    _pw = types.ModuleType("playwright")
    _pw_api: Any = types.ModuleType("playwright.async_api")
    _pw_api.async_playwright = MagicMock()
    _pw_api.Page = MagicMock()
    _pw_api.Browser = MagicMock()
    _pw_api.Playwright = MagicMock()
    sys.modules["playwright"] = _pw
    sys.modules["playwright.async_api"] = _pw_api

for _mod in ("whisper", "torch", "onnxruntime", "sounddevice",
             "openwakeword", "kokoro_onnx"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

if "app.services.voice" not in sys.modules:
    _voice_stub: Any = types.ModuleType("app.services.voice")
    _voice_stub.get_voice_service = MagicMock()
    sys.modules["app.services.voice"] = _voice_stub

from app.skills.web.browser import BrowserController

def _make_controller(engine_name: str) -> BrowserController:
    with patch("app.skills.web.browser.ConfigManager") as mock_cm:
        mock_cm.get.return_value = {"automation_browser": engine_name}
        return BrowserController()

def _mock_engine():
    """Returns (mock_browser, mock_engine). engine.launch() returns mock_browser."""
    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = False
    mock_browser.new_page = AsyncMock(return_value=MagicMock())
    mock_engine = MagicMock()
    mock_engine.connect = AsyncMock(side_effect=Exception("no remote"))
    mock_engine.launch = AsyncMock(return_value=mock_browser)
    return mock_browser, mock_engine

def _run_get_page(ctrl: BrowserController, visible: bool, mock_playwright: MagicMock) -> None:
    async def _run():
        ctrl._lock = asyncio.Lock()
        ctrl.playwright = mock_playwright
        await ctrl.get_page(visible=visible)
    asyncio.run(_run())

def test_defaults_to_chromium_when_config_absent():
    with patch("app.skills.web.browser.ConfigManager") as mock_cm:
        mock_cm.get.return_value = {}
        ctrl = BrowserController()
    assert ctrl._engine_name == "chromium"

def test_selects_firefox_from_config():
    assert _make_controller("firefox")._engine_name == "firefox"

def test_selects_webkit_from_config():
    assert _make_controller("webkit")._engine_name == "webkit"

def test_unknown_engine_falls_back_to_chromium():
    assert _make_controller("lynx")._engine_name == "chromium"

def test_remote_connect_attempted_for_chromium():
    ctrl = _make_controller("chromium")
    _, engine = _mock_engine()
    mock_pw = MagicMock()
    mock_pw.chromium = engine
    _run_get_page(ctrl, visible=True, mock_playwright=mock_pw)
    engine.connect.assert_called_once()
    engine.launch.assert_called_once_with(headless=False)   # fallback after connect fails

def test_remote_connect_skipped_for_firefox():
    ctrl = _make_controller("firefox")
    _, engine = _mock_engine()
    mock_pw = MagicMock()
    mock_pw.firefox = engine
    _run_get_page(ctrl, visible=True, mock_playwright=mock_pw)
    engine.connect.assert_not_called()
    engine.launch.assert_called_once_with(headless=False)

def test_remote_connect_skipped_for_webkit():
    ctrl = _make_controller("webkit")
    _, engine = _mock_engine()
    mock_pw = MagicMock()
    mock_pw.webkit = engine
    _run_get_page(ctrl, visible=False, mock_playwright=mock_pw)
    engine.connect.assert_not_called()
    engine.launch.assert_called_once_with(headless=True)

def test_headless_launch_for_firefox():
    ctrl = _make_controller("firefox")
    _, engine = _mock_engine()
    mock_pw = MagicMock()
    mock_pw.firefox = engine
    _run_get_page(ctrl, visible=False, mock_playwright=mock_pw)
    engine.launch.assert_called_once_with(headless=True)