import sys
import webbrowser

from app.core.config import ConfigManager

_BROWSER_KEYS: dict[str, list[str]] = {
    "chrome":   ["google-chrome", "chrome"],
    "firefox":  ["firefox"],
    "edge":     ["microsoft-edge"],
    "safari":   ["safari"],
    "opera":    ["opera"],
}

def open_ui(url: str, browser_override: str | None = None) -> None:
    name = (browser_override or ConfigManager.get().get("preferred_browser", "")).strip().lower()

    if not name:
        webbrowser.open(url)
        return

    if name == "safari" and sys.platform != "darwin":
        print("[wade] Safari is only available on macOS — using system default.")
        webbrowser.open(url)
        return

    keys = _BROWSER_KEYS.get(name)
    if not keys:
        print(f"[wade] Browser '{name}' not found — using system default.")
        webbrowser.open(url)
        return

    for key in keys:
        try:
            webbrowser.get(key).open(url)
            return
        except webbrowser.Error:
            continue

    print(f"[wade] '{name}' browser binary not available — using system default.")
    webbrowser.open(url)