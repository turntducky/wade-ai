import copy
import yaml
import asyncio
import threading

from typing import Any
from pathlib import Path

DUCK_HOME          = Path.home() / ".wade"
CONFIG_FILE        = DUCK_HOME / "config.yaml"
DATA_DIR           = DUCK_HOME / "data"
WORKSPACE_DIR      = DUCK_HOME / "workspace"
LOG_FILE           = DUCK_HOME / "gateway.log"
BRIDGE_LOG_FILE    = DUCK_HOME / "whatsapp-bridge.log"
MODEL_LOCK_FILE    = DUCK_HOME / "models.lock"
PID_FILE           = DUCK_HOME / "gateway.pid"
VOICE_DIR          = DATA_DIR / "voices"
SKILLS_DIR         = DUCK_HOME / "skills"
TASKS_DB_PATH      = DUCK_HOME / "tasks.db"
SKILLS_SESSION_DIR = SKILLS_DIR / "session"
SKILLS_PENDING_DIR = SKILLS_DIR / "pending"
EPISODES_DB_PATH   = DUCK_HOME / "memory" / "episodes.db"
MONITORS_USER_DIR  = DUCK_HOME / "monitors"

def setup_directories():
    """Create all required user-owned directories on first run."""
    memory_dir = DUCK_HOME / "memory"
    for d in (DUCK_HOME, DATA_DIR, WORKSPACE_DIR, VOICE_DIR, SKILLS_DIR,
              SKILLS_SESSION_DIR, SKILLS_PENDING_DIR, memory_dir, MONITORS_USER_DIR):
        d.mkdir(parents=True, exist_ok=True)

setup_directories()

def migrate_legacy_root_config() -> None:
    """On first run, migrate config.yaml from the project root (legacy) to the new location in the user's home directory."""
    import shutil

    try:
        project_root = Path(__file__).resolve().parent.parent
    except Exception:
        return

    legacy = project_root / "config.yaml"
    migrated_marker = project_root / "config.yaml.migrated"

    if not legacy.exists() or migrated_marker.exists():
        return

    try:
        if not CONFIG_FILE.exists():
            shutil.copy(legacy, CONFIG_FILE)
            print(f"[wade] Config migrated from {legacy} to {CONFIG_FILE}")
        legacy.rename(migrated_marker)
        print("[wade] Old config.yaml renamed to config.yaml.migrated.")
    except Exception as exc:
        print(f"[wade] Warning: could not complete config migration: {exc}")

migrate_legacy_root_config()

def get_package_dir() -> Path:
    """Returns the filesystem path to the 'app' package, which is used as the base for relative paths to static assets and templates."""
    # Anchored to this file's real location so it works regardless of sys.path
    # ordering or whether another package named 'app' exists in site-packages.
    # config.py lives at app/core/config.py, so two .parent calls reach app/.
    return Path(__file__).resolve().parent.parent

class ConfigManager:
    _cache = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> dict[str, Any]:
        """Returns a thread-safe deep copy of the configuration."""
        with cls._lock:
            if cls._cache is None:
                if CONFIG_FILE.exists():
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        cls._cache = yaml.safe_load(f) or {}
                else:
                    cls._cache = {}
            return copy.deepcopy(cls._cache)

    @classmethod
    def save(cls, config_data: dict):
        """Thread-safe, atomic save of the configuration to both memory and disk."""
        with cls._lock:
            cls._cache = copy.deepcopy(config_data)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(cls._cache, f, default_flow_style=False, sort_keys=False)
            tmp.replace(CONFIG_FILE)

    @classmethod
    def reload(cls):
        """Invalidates the in-memory cache and forces a reload from disk on the next get()."""
        with cls._lock:
            cls._cache = None

    @classmethod
    def is_configured(cls) -> bool:
        """Returns True if a config file exists (i.e. setup has been run)."""
        return CONFIG_FILE.exists()

    @classmethod
    def get_user_name(cls) -> str:
        """Returns the user's name from config or fallback to USER.md."""
        config = cls.get()
        name = config.get("user_name")
        if name:
            return name

        import re
        user_md = WORKSPACE_DIR / "USER.md"
        if user_md.exists():
            try:
                content = user_md.read_text(encoding="utf-8")
                match = re.search(r"(?i)[-\s]*\*\*Name:\*\*\s*(.+)", content)
                if not match:
                    match = re.search(r"(?i)[-\s]*Name:\s*(.+)", content)

                if match:
                    val = match.group(1).strip()
                    if val.lower() != "unknown":
                        return val
            except Exception:
                pass

        return "User"

    @classmethod
    def get_assistant_name(cls) -> str:
        """Returns the assistant's name from config or defaults to 'W.A.D.E.'"""
        return cls.get().get("assistant_name") or "W.A.D.E."

    @classmethod
    async def async_get(cls) -> dict:
        """Async-safe version of get() — offloads to a thread so the event loop is never blocked."""
        return await asyncio.to_thread(cls.get)