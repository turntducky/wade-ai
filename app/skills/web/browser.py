from __future__ import annotations

import os
import time
import asyncio
import tempfile

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page, Browser, Playwright

try:
    from playwright.async_api import async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    async_playwright = None  # type: ignore

from app.skills.registry import register_tool
from app.core.config import ConfigManager
from app.core.utils import safe_truncate

BROWSER_HOST = os.getenv("BROWSER_HOST", "localhost")
MAX_TOOL_OUTPUT_LENGTH = 1500

_ENGINE_MAP = {
    "chromium": lambda p: p.chromium,
    "firefox":  lambda p: p.firefox,
    "webkit":   lambda p: p.webkit,
}

class BrowserController:
    """Connects to remote browser sessions with a robust local fallback."""

    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.headless_browser: Optional[Browser] = None
        self.headed_browser: Optional[Browser] = None
        self.headless_page: Optional[Page] = None
        self.headed_page: Optional[Page] = None
        self.is_local: bool = False
        self._lock = asyncio.Lock()

        engine_name = ConfigManager.get().get("automation_browser", "chromium")
        if engine_name not in _ENGINE_MAP:
            engine_name = "chromium"
        self._engine_name = engine_name

    async def get_page(self, visible: bool) -> Page:
        """Connects to remote browser or launches local fallback."""
        if not _PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright is not installed. "
                "Run: pip install playwright && playwright install chromium"
            )
        async with self._lock:
            if not self.playwright:
                self.playwright = await async_playwright().start()

            engine = _ENGINE_MAP[self._engine_name](self.playwright)

            if visible:
                if not self.headed_browser or not self.headed_browser.is_connected():
                    if self._engine_name == "chromium":
                        ws_url = f"ws://{BROWSER_HOST}:9222/playwright"
                        try:
                            print(f"🔌 Attempting connection to Remote VISIBLE Browser at {ws_url}...")
                            self.headed_browser = await engine.connect(ws_url, timeout=5000)
                            self.is_local = False
                        except Exception:
                            print("⚠️ Remote visible browser unavailable. Launching LOCAL fallback...")
                            self.headed_browser = await engine.launch(headless=False)
                            self.is_local = True
                    else:
                        self.headed_browser = await engine.launch(headless=False)
                        self.is_local = True

                assert self.headed_browser is not None
                self.headed_page = await self.headed_browser.new_page()
                assert self.headed_page is not None
                return self.headed_page

            else:
                if not self.headless_browser or not self.headless_browser.is_connected():
                    if self._engine_name == "chromium":
                        ws_url = f"ws://{BROWSER_HOST}:9223/playwright"
                        try:
                            print(f"🔌 Attempting connection to Remote HEADLESS Browser at {ws_url}...")
                            self.headless_browser = await engine.connect(ws_url, timeout=5000)
                            self.is_local = False
                        except Exception:
                            print("⚠️ Remote headless browser unavailable. Launching LOCAL fallback...")
                            self.headless_browser = await engine.launch(headless=True)
                            self.is_local = True
                    else:
                        self.headless_browser = await engine.launch(headless=True)
                        self.is_local = True

                assert self.headless_browser is not None
                self.headless_page = await self.headless_browser.new_page()
                assert self.headless_page is not None
                return self.headless_page

    async def close_all(self):
        async with self._lock:
            if self.headed_browser:
                await self.headed_browser.close()
            if self.headless_browser:
                await self.headless_browser.close()
            if self.playwright:
                await self.playwright.stop()
            self.headed_browser = None
            self.headless_browser = None
            self.playwright = None
            self.headed_page = None
            self.headless_page = None

browser_controller = BrowserController()

@register_tool("control_browser")
async def control_browser(action: str, visible: bool, target: str = "", value: str = "") -> str:
    """Executes the requested browser action with automatic status reporting."""
    try:
        if action == "close":
            await browser_controller.close_all()
            return "Closed all active browser sessions."

        page = await browser_controller.get_page(visible=visible)
        mode = "Visible" if visible else "Hidden"
        origin = "Local Fallback" if browser_controller.is_local else "Remote Service"
        status_prefix = f"[{mode} Browser via {origin}]"

        if action == "navigate":
            if not target.startswith("http"):
                target = "https://" + target
            await page.goto(target, wait_until="domcontentloaded", timeout=20000)
            return f"{status_prefix} Navigated to {page.url} successfully."

        elif action == "click":
            await page.click(target, timeout=8000)
            return f"{status_prefix} Clicked element '{target}'."

        elif action == "type":
            await page.fill(target, value, timeout=8000)
            return f"{status_prefix} Typed into '{target}'."

        elif action == "select_option":
            await page.select_option(target, value, timeout=8000)
            return f"{status_prefix} Selected '{value}' in dropdown '{target}'."

        elif action == "check":
            await page.check(target, timeout=8000)
            return f"{status_prefix} Checked '{target}'."

        elif action == "uncheck":
            await page.uncheck(target, timeout=8000)
            return f"{status_prefix} Unchecked '{target}'."

        elif action == "wait_for_selector":
            await page.wait_for_selector(target, timeout=10000)
            return f"{status_prefix} Element '{target}' is present and ready."

        elif action == "screenshot":
            screenshot_dir = os.path.join(tempfile.gettempdir(), "wade_screenshots")
            os.makedirs(screenshot_dir, exist_ok=True)
            path = os.path.join(screenshot_dir, f"wade_{int(time.time())}.png")
            await page.screenshot(path=path, full_page=False)
            return f"{status_prefix} Screenshot saved to: {path}"

        elif action == "extract_text":
            try:
                if target:
                    try:
                        text = await page.locator(target).inner_text(timeout=8000)
                    except Exception:
                        text = await page.locator("body").inner_text(timeout=8000)
                else:
                    text = await page.locator("body").inner_text(timeout=8000)
                clean_text = " ".join(text.split())
                truncated = safe_truncate(clean_text, MAX_TOOL_OUTPUT_LENGTH)
                return f"<browser_content origin='{origin}' mode='{mode}' url='{page.url}'>\n{truncated}\n</browser_content>"
            except Exception as e:
                return f"{status_prefix} Extraction Error: {str(e)}"

        elif action == "evaluate_js":
            result = await page.evaluate(target)
            return f"<browser_js_result origin='{origin}'>\n{str(result)}\n</browser_js_result>"

        return f"Error: Unknown action '{action}'."

    except Exception as e:
        err_msg = str(e)
        if "ECONNREFUSED" in err_msg or "Connection closed" in err_msg:
            return (
                f"Browser Connection Error: {err_msg}. "
                "I attempted local fallback but it may have failed if binaries are missing. "
                "Suggestion: Run 'perform_system_recovery(action=\"provision_browser_service\")' to fix."
            )
        return f"Browser Error ({action}): {err_msg}"