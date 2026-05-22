import logging
import threading
from pathlib import Path

logger = logging.getLogger("wade_md_patcher")

WORKSPACE_DIR = Path.home() / ".wade" / "workspace"

_patch_lock = threading.Lock()

PATCH_MAP: dict[str, dict] = {
    "User: Name":       {"file": "USER.md", "sentinel": "- **Name:** Unknown",       "template": "- **Name:** {value}"},
    "User: Nickname":   {"file": "USER.md", "sentinel": "- **Nickname:** Unknown",   "template": "- **Nickname:** {value}"},
    "User: Age":        {"file": "USER.md", "sentinel": "- **Age:** Unknown",         "template": "- **Age:** {value}"},
    "User: Birthday":   {"file": "USER.md", "sentinel": "- **Birthday:** Unknown",   "template": "- **Birthday:** {value}"},
    "User: Occupation": {"file": "USER.md", "sentinel": "- **Occupation:** Unknown", "template": "- **Occupation:** {value}"},
    "User: Location":   {"file": "USER.md", "sentinel": "- **Location:** Unknown",   "template": "- **Location:** {value}"},
    "User: Timezone":   {"file": "USER.md", "sentinel": "- **Timezone:** Unknown",   "template": "- **Timezone:** {value}"},
}

def _patch_md_field(file_path: Path, sentinel: str, replacement: str) -> bool:
    """Replace sentinel with replacement in file_path. Returns True if patched, False if no-op. Never raises."""
    try:
        with _patch_lock:
            if not file_path.exists():
                return False
            text = file_path.read_text(encoding="utf-8")
            if sentinel not in text:
                return False
            file_path.write_text(text.replace(sentinel, replacement, 1), encoding="utf-8")
        return True
    except Exception as exc:
        logger.warning("[MdPatcher] Failed to patch %s: %s", getattr(file_path, "name", str(file_path)), exc)
        return False

def patch_if_mapped(topic_key: str, value: str) -> None:
    """Patch the workspace .md file for topic_key if it has a known sentinel mapping."""
    entry = PATCH_MAP.get(topic_key)
    if not entry:
        return
    patched = _patch_md_field(
        WORKSPACE_DIR / entry["file"],
        entry["sentinel"],
        entry["template"].format(value=value),
    )
    if patched:
        logger.debug("[MdPatcher] Patched '%s' in %s", topic_key, entry["file"])