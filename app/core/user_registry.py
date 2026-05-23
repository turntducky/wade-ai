from __future__ import annotations

import re
import time
import httpx
import logging
import asyncio

from pathlib import Path
from typing import FrozenSet
from dataclasses import dataclass

logger = logging.getLogger("wade.user_registry")

WADE_HOME      = Path.home() / ".wade"
REGISTRY_PATH  = WADE_HOME / "users.yaml"
TIER_BASE      = WADE_HOME / "tiers"
ADMIN_WORKSPACE = WADE_HOME / "workspace"

_TIER_TOOLS: dict[str, FrozenSet[str]] = {
    "admin":     frozenset(),
    "family":    frozenset({"web", "realtime", "math", "python", "scheduling", "memory_write"}),
    "friends":   frozenset({"web", "realtime", "math", "memory_write"}),
    "guests":    frozenset({"web", "realtime", "math"}),
    "strangers": frozenset({"web", "realtime"}),
}

_TIER_MEMORY_WRITE: dict[str, bool] = {
    "admin":     True,
    "family":    True,
    "friends":   True,
    "guests":    True,
    "strangers": True,
}

@dataclass(frozen=True)
class TierContext:
    """Encapsulates the workspace, memory, and tool access context for a given user tier."""
    tier: str
    workspace_dir: Path
    memory_dir: Path
    allowed_tool_categories: FrozenSet[str]
    allow_memory_write: bool
    session_prefix: str

    @staticmethod
    def for_tier(tier: str) -> "TierContext":
        workspace = ADMIN_WORKSPACE if tier == "admin" else TIER_BASE / tier
        allowed = _TIER_TOOLS.get(tier, frozenset())
        try:
            from app.core.config import ConfigManager
            overrides = ConfigManager.get().get("tier_permissions", {})
            if tier in overrides and isinstance(overrides[tier], list):
                allowed = frozenset(overrides[tier])
        except Exception:
            pass
        return TierContext(
            tier=tier,
            workspace_dir=workspace,
            memory_dir=workspace / "memory",
            allowed_tool_categories=allowed,
            allow_memory_write=_TIER_MEMORY_WRITE.get(tier, False),
            session_prefix="" if tier == "admin" else f"wa_{tier}_",
        )

    @staticmethod
    def admin() -> "TierContext":
        return TierContext.for_tier("admin")

    @property
    def is_admin(self) -> bool:
        return self.tier == "admin"

    @property
    def is_restricted(self) -> bool:
        """True for all non-admin tiers (tool access is limited)."""
        return bool(self.allowed_tool_categories)

    def session_id_for(self, sender: str) -> str:
        """Derives a session ID for a given WhatsApp sender. For admin, returns the sender as-is (caller manages this). For non-admin tiers, normalizes the sender to extract digits and prefixes with the session_prefix to create a unique session ID per contact (e.g. "wa_friends_2561234567")."""
        if self.is_admin:
            return sender
        digits = re.sub(r"\D", "", sender)
        return f"{self.session_prefix}{digits}"

    def user_memory_dir(self, session_id: str) -> Path:
        """Returns the memory directory for a given session ID. Admin sessions share the top-level memory dir; non-admin sessions are namespaced per WhatsApp contact by the digits extracted from the session ID. """
        if self.is_admin:
            return self.memory_dir
        digits_match = re.search(r"(\d+)$", session_id)
        if digits_match:
            user_dir = self.memory_dir / digits_match.group(1)
            user_dir.mkdir(parents=True, exist_ok=True)
            return user_dir
        return self.memory_dir
    
class UserRegistry:
    """Maintains the mapping of WhatsApp senders and browser devices to user tiers based on users.yaml."""
    RELOAD_INTERVAL = 60.0
    BRIDGE_SYNC_INTERVAL = 300.0

    def __init__(self) -> None:
        self._data: dict[str, list[str]] = {}
        self._browser_ids: dict[str, str] = {}
        self._lid_map: dict[str, str] = {}
        self._loaded_at: float = 0.0
        self._bridge_synced_at: float = 0.0

    def resolve(self, sender: str) -> TierContext:
        """Resolves a WhatsApp sender to their TierContext. Handles LID resolution by checking cached bridge mappings."""
        self._ensure_loaded()
        self._ensure_bridge_synced()
        
        jid = _normalize_jid(sender)
        
        if jid.endswith("@lid.us") and jid in self._lid_map:
            resolved = self._lid_map[jid]
            logger.debug("[REGISTRY] Resolved LID %s → %s", jid, resolved)
            jid = resolved

        for tier in ("admin", "family", "friends", "guests"):
            if jid in self._data.get(tier, []):
                logger.debug("[REGISTRY] %s → %s", jid, tier)
                return TierContext.for_tier(tier)
        
        logger.debug("[REGISTRY] %s → strangers (not listed)", jid)
        return TierContext.for_tier("strangers")

    def _ensure_bridge_synced(self) -> None:
        """Fetch contact mappings from the WhatsApp bridge periodically."""
        now = time.monotonic()
        if now - self._bridge_synced_at < self.BRIDGE_SYNC_INTERVAL:
            return
        
        self._bridge_synced_at = now
        asyncio.create_task(self._sync_with_bridge())

    async def _sync_with_bridge(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                from app.services.messenger import BAILEYS_API_URL
                resp = await client.get(f"{BAILEYS_API_URL}/lid-map")
                if resp.status_code == 200:
                    self._lid_map = resp.json()
                    logger.debug("[REGISTRY] Synced %d LID mappings", len(self._lid_map))
        except Exception as e:
            logger.debug("[REGISTRY] Bridge sync failed: %s", e)

    def resolve_device(self, device_id: str) -> "TierContext | None":
        """Resolve a browser device UUID to a TierContext. Returns None if not registered."""
        self._ensure_loaded()
        tier = self._browser_ids.get(str(device_id).strip())
        if tier:
            logger.debug("[REGISTRY] Device ...%s → %s", device_id[-8:], tier)
            return TierContext.for_tier(tier)
        return None

    def local_context(self) -> TierContext:
        """The local browser / Tauri desktop is always admin."""
        return TierContext.admin()

    def get_admin_jids(self) -> list[str]:
        """Returns the WhatsApp JIDs registered as admin tier in users.yaml."""
        self._ensure_loaded()
        return list(self._data.get("admin", []))

    def _ensure_loaded(self) -> None:
        now = time.monotonic()
        if now - self._loaded_at < self.RELOAD_INTERVAL and self._data is not None:
            return
        self._load()

    def _load(self) -> None:
        if not REGISTRY_PATH.exists():
            logger.warning(
                "[REGISTRY] users.yaml not found at %s — all WhatsApp senders will be 'strangers'",
                REGISTRY_PATH,
            )
            self._data = {}
            self._browser_ids = {}
            self._loaded_at = time.monotonic()
            return
        try:
            raw = _parse_users_yaml(REGISTRY_PATH)
            self._data = {
                tier: [_normalize_jid(jid) for jid in jids]
                for tier, jids in raw.items()
            }
            self._browser_ids = _parse_browser_ids_yaml(REGISTRY_PATH)
            self._loaded_at = time.monotonic()
            logger.info(
                "[REGISTRY] Loaded users.yaml — %s",
                {t: len(v) for t, v in self._data.items()},
            )
        except Exception as exc:
            logger.error("[REGISTRY] Failed to load users.yaml: %s", exc)
            self._data = {}
            self._browser_ids = {}
            self._loaded_at = time.monotonic()

def _normalize_jid(raw: str) -> str:
    """Normalizes a WhatsApp sender string to a JID format for matching against users.yaml. Handles various input formats such as raw phone numbers, JIDs with or without the "@s.whatsapp.net" suffix, and Baileys multi-device formats (e.g. "12566778216:7"). The output is always in the form of "{phone_number}@s.whatsapp.net"."""
    raw = raw.strip()
    if "@" in raw:
        local, _, domain = raw.partition("@")
        local = local.split(":")[0]
        return f"{local}@{domain}"
    digits = re.sub(r"\D", "", raw)
    return f"{digits}@s.whatsapp.net"

def _parse_browser_ids_yaml(path: Path) -> dict[str, str]:
    """Parse browser_device_ids from users.yaml. Returns {device_uuid: tier_name}."""
    try:
        import yaml  # type: ignore
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        raw = _parse_yaml_minimal(path)

    result: dict[str, str] = {}
    for tier, block in raw.items():
        if not isinstance(block, dict):
            continue
        ids = block.get("browser_device_ids") or []
        if isinstance(ids, list):
            for did in ids:
                if did:
                    result[str(did).strip()] = tier
    return result

def _parse_users_yaml(path: Path) -> dict[str, list[str]]:
    """Parses the users.yaml file to extract the mapping of tiers to WhatsApp JIDs. Tries to use PyYAML if available; if not, falls back to a minimal custom parser that can handle the expected structure of users.yaml (tiers with nested whatsapp lists). The returned dict maps tier names to lists of normalized JIDs."""
    try:
        import yaml  # type: ignore
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        raw = _parse_yaml_minimal(path)

    result: dict[str, list[str]] = {}
    for tier, block in raw.items():
        if not isinstance(block, dict):
            continue
        jids = block.get("whatsapp") or []
        if isinstance(jids, list):
            result[tier] = [str(j) for j in jids if j]
    return result

def _parse_yaml_minimal(path: Path) -> dict:
    """Minimal YAML parser for users.yaml when PyYAML is unavailable. Handles whatsapp and browser_device_ids lists."""
    result: dict = {}
    current_tier: str | None = None
    in_whatsapp = False
    in_browser_ids = False

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tier_match = re.match(r'^([a-z_]+):$', stripped)
        if tier_match:
            current_tier = tier_match.group(1)
            result[current_tier] = {"whatsapp": [], "browser_device_ids": []}
            in_whatsapp = False
            in_browser_ids = False
            continue
        if stripped == "whatsapp:":
            in_whatsapp = True
            in_browser_ids = False
            continue
        if stripped == "browser_device_ids:":
            in_browser_ids = True
            in_whatsapp = False
            continue
        if stripped.startswith("- ") and current_tier:
            value = stripped[2:].split("#")[0].strip().strip('"').strip("'")
            if value:
                if in_whatsapp:
                    result[current_tier]["whatsapp"].append(value)
                elif in_browser_ids:
                    result[current_tier]["browser_device_ids"].append(value)
    return result

user_registry = UserRegistry()