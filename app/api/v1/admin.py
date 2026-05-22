from __future__ import annotations

import re
import logging

from typing import Any
from pathlib import Path

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends

logger = logging.getLogger(__name__)

from app.core.security import require_admin
from app.core.user_registry import (
    WADE_HOME, TIER_BASE, REGISTRY_PATH, _normalize_jid
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

_NON_ADMIN_TIERS = ("family", "friends", "guests", "strangers")
_ALL_REGISTRY_TIERS = ("admin", "family", "friends", "guests")

class TierChangeRequest(BaseModel):
    jid: str
    new_tier: str
    admin_confirm: bool = False

class UserRegistrationRequest(BaseModel):
    phone: str
    tier: str
    admin_confirm: bool = False

def _parse_memory_file(path: Path) -> list[dict[str, str]]:
    """Parse a W.A.D.E. memory .md file into a list of {role, text} dicts."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return []
    messages: list[dict[str, str]] = []
    for block in content.split("\n\n---\n\n"):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^###\s+(\w+)\s*\n(.*)", block, re.DOTALL)
        if m:
            role = m.group(1).lower()
            text = m.group(2).strip()
            if text:
                messages.append({"role": role, "text": text})
    return messages

def _format_phone(digits: str) -> str:
    """Format a raw digit string for display: +12561234567."""
    return f"+{digits}" if digits else digits

def _stem_to_date(stem: str) -> str:
    """Convert 'MM-DD-YY' filename stem to a readable date label."""
    parts = stem.split("-")
    if len(parts) == 3:
        months = ["Jan","Feb","Mar","Apr","May","Jun",
                  "Jul","Aug","Sep","Oct","Nov","Dec"]
        try:
            m = int(parts[0]) - 1
            d = int(parts[1])
            y = 2000 + int(parts[2])
            return f"{months[m]} {d}, {y}"
        except (ValueError, IndexError):
            pass
    return stem

async def _scan_active_users() -> list[dict[str, Any]]:
    """Scan the memory directories for all tiers and enrich with bridge contacts."""
    from app.services.messenger import get_whatsapp_contacts
    
    bridge_contacts = {}
    try:
        contacts_list = await get_whatsapp_contacts()
        for c in contacts_list:
            bridge_contacts[c["jid"]] = c
    except Exception as e:
        logger.warning(f"Could not fetch bridge contacts: {e}")

    users: list[dict[str, Any]] = []
    
    lid_map = {}

    for tier in _NON_ADMIN_TIERS:
        tier_memory = TIER_BASE / tier / "memory"
        if not tier_memory.exists():
            continue
            
        for user_dir in sorted(tier_memory.iterdir()):
            if not user_dir.is_dir():
                continue
                
            raw_id = user_dir.name
            
            jid = f"{raw_id}@s.whatsapp.net"
            if len(raw_id) > 13:
                jid = f"{raw_id}@lid.us"
            
            contact = bridge_contacts.get(jid)
            
            display_name = contact["name"] if contact and contact.get("name") else _format_phone(raw_id)
            phone_display = contact["phone"] if contact and contact.get("phone") else _format_phone(raw_id)

            md_files = sorted(user_dir.glob("*.md"), reverse=True)
            if not md_files:
                continue
                
            total_msg = 0
            for f in md_files:
                try:
                    content = f.read_text(encoding="utf-8")
                    total_msg += content.count("### user\n")
                except Exception:
                    pass
                    
            users.append({
                "user_key":    f"{tier}_{raw_id}",
                "phone":       phone_display,
                "display":     display_name,
                "tier":        tier,
                "jid":         jid,
                "last_active": _stem_to_date(md_files[0].stem),
                "last_active_raw": md_files[0].stem,
                "message_count": total_msg,
                "days_active": len(md_files),
                "has_history": True,
            })
    return users

def _read_registry() -> dict[str, dict[str, list[str]]]:
    """Read users.yaml → {tier: {whatsapp: [...], browser_device_ids: [...]}}."""
    if not REGISTRY_PATH.exists():
        return {}
    try:
        import yaml
        raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
        result: dict[str, dict[str, list[str]]] = {}
        for tier, block in raw.items():
            if isinstance(block, dict):
                result[tier] = {
                    "whatsapp": [str(j) for j in (block.get("whatsapp") or []) if j],
                    "browser_device_ids": [str(d) for d in (block.get("browser_device_ids") or []) if d],
                }
        return result
    except Exception:
        return {}

def _write_registry(registry: dict[str, dict[str, list[str]]]) -> None:
    """Write an updated registry back to users.yaml via PyYAML (atomic: write-then-rename)."""
    try:
        import yaml
    except ImportError:
        raise HTTPException(status_code=500, detail="PyYAML not installed")
    data: dict[str, Any] = {}
    for tier in _ALL_REGISTRY_TIERS:
        block = registry.get(tier, {})
        data[tier] = {
            "whatsapp": block.get("whatsapp", []),
            "browser_device_ids": block.get("browser_device_ids", []),
        }
    header = (
        "# W.A.D.E. User Registry — managed by admin panel\n"
        "# Tiers: admin, family, friends, guests (strangers = automatic fallback)\n\n"
    )
    body = yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    tmp = REGISTRY_PATH.with_suffix(".yaml.tmp")
    tmp.write_text(header + body, encoding="utf-8")
    tmp.replace(REGISTRY_PATH)

@router.get("/users")
async def list_users(tier: str | None = None) -> dict[str, Any]:
    """List all users with conversation history, along with metadata like last active date and message count. If `tier` query param is provided, filter to that tier only."""
    active = await _scan_active_users()
    registry = _read_registry()

    active_jids = {u["jid"] for u in active}

    for reg_tier in _ALL_REGISTRY_TIERS:
        for jid in registry.get(reg_tier, {}).get("whatsapp", []):
            if jid not in active_jids:
                phone = re.sub(r"\D", "", jid.split("@")[0])
                active.append({
                    "user_key":      f"{reg_tier}_{phone}",
                    "phone":         phone,
                    "display":       _format_phone(phone),
                    "tier":          reg_tier,
                    "jid":           jid,
                    "last_active":   "Never",
                    "last_active_raw": "",
                    "message_count": 0,
                    "days_active":   0,
                    "has_history":   False,
                })

    active.sort(key=lambda u: u["last_active_raw"] or "", reverse=True)

    if tier:
        active = [u for u in active if u["tier"] == tier]

    by_tier: dict[str, int] = {t: 0 for t in list(_NON_ADMIN_TIERS) + ["admin"]}
    for u in active:
        by_tier[u["tier"]] = by_tier.get(u["tier"], 0) + 1

    return {
        "users": active,
        "summary": {
            "total": len(active),
            "by_tier": by_tier,
        },
    }

@router.get("/users/{tier}/{phone}/history")
async def get_user_history(tier: str, phone: str) -> dict[str, Any]:
    """Get the conversation history for one user, organized by date. Returns {"user": {...}, "history": [{"date": ..., "messages": [...]}, ...]} where messages are lists of {role, text} dicts. If no history is found, returns an empty history list."""
    if tier not in _NON_ADMIN_TIERS:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {tier}")

    user_dir = TIER_BASE / tier / "memory" / phone
    if not user_dir.exists():
        return {"user": {"tier": tier, "phone": phone, "display": _format_phone(phone)}, "history": []}

    md_files = sorted(user_dir.glob("*.md"))
    history: list[dict[str, Any]] = []
    for f in md_files:
        messages = _parse_memory_file(f)
        if messages:
            history.append({
                "date": _stem_to_date(f.stem),
                "date_raw": f.stem,
                "messages": messages,
            })

    return {
        "user": {
            "tier":    tier,
            "phone":   phone,
            "display": _format_phone(phone),
            "jid":     f"{phone}@s.whatsapp.net",
        },
        "history": history,
    }

@router.post("/users")
async def register_user(req: UserRegistrationRequest) -> dict[str, str]:
    """Register a new WhatsApp number to a tier in users.yaml."""
    if req.tier not in set(_ALL_REGISTRY_TIERS):
        raise HTTPException(status_code=400, detail=f"Invalid tier: {req.tier}")
    if req.tier == "admin" and not req.admin_confirm:
        raise HTTPException(status_code=403, detail="admin_confirm required to assign admin tier")

    jid = _normalize_jid(req.phone)
    digits = re.sub(r"\D", "", jid.split("@")[0])
    if len(digits) < 7:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    registry = _read_registry()
    for t in _ALL_REGISTRY_TIERS:
        if jid in registry.get(t, {}).get("whatsapp", []):
            raise HTTPException(status_code=409, detail=f"Number already registered under {t}")

    registry.setdefault(req.tier, {}).setdefault("whatsapp", []).append(jid)
    _write_registry(registry)

    from app.core.user_registry import user_registry
    user_registry._loaded_at = 0.0

    return {"status": "ok", "jid": jid, "tier": req.tier}

@router.delete("/users/{tier}/{phone}")
async def unregister_user(tier: str, phone: str) -> dict[str, str]:
    """Remove a WhatsApp number from users.yaml. History is preserved; the contact reverts to strangers."""
    if tier not in set(_ALL_REGISTRY_TIERS):
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier}")

    jid = _normalize_jid(phone)
    registry = _read_registry()
    wa_list = registry.get(tier, {}).get("whatsapp", [])
    if jid not in wa_list:
        raise HTTPException(status_code=404, detail="Number not registered in this tier")

    wa_list.remove(jid)
    _write_registry(registry)

    from app.core.user_registry import user_registry
    user_registry._loaded_at = 0.0

    return {"status": "ok", "jid": jid}

@router.patch("/users/tier")
async def change_user_tier(req: TierChangeRequest) -> dict[str, str]:
    """Change a user's tier membership. Updates users.yaml and moves their memory files."""
    valid_tiers = set(_ALL_REGISTRY_TIERS)
    if req.new_tier not in valid_tiers:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {req.new_tier}")
    if req.new_tier == "admin" and not req.admin_confirm:
        raise HTTPException(status_code=403, detail="admin_confirm required to assign admin tier")

    jid = _normalize_jid(req.jid)
    registry = _read_registry()

    old_tier: str | None = None
    for t in _ALL_REGISTRY_TIERS:
        wa_list = registry.get(t, {}).get("whatsapp", [])
        if jid in wa_list:
            old_tier = t
            wa_list.remove(jid)

    registry.setdefault(req.new_tier, {}).setdefault("whatsapp", []).append(jid)
    _write_registry(registry)

    if old_tier and old_tier != req.new_tier:
        phone = re.sub(r"\D", "", jid.split("@")[0])
        src = TIER_BASE / old_tier / "memory" / phone
        dst = TIER_BASE / req.new_tier / "memory" / phone
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)

    from app.core.user_registry import user_registry
    user_registry._loaded_at = 0.0

    return {"status": "ok", "jid": jid, "tier": req.new_tier}

@router.delete("/users/{tier}/{phone}/history")
async def clear_user_history(tier: str, phone: str) -> dict[str, str]:
    """Remove all conversation history files for one user."""
    if tier not in _NON_ADMIN_TIERS and tier != "strangers":
        raise HTTPException(status_code=400, detail=f"Unknown tier: {tier}")

    user_dir = TIER_BASE / tier / "memory" / phone
    if not user_dir.exists():
        return {"status": "ok", "detail": "No history found"}

    deleted = 0
    for f in user_dir.glob("*.md"):
        try:
            f.unlink()
            deleted += 1
        except Exception:
            pass

    return {"status": "ok", "deleted": str(deleted)}

@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Quick overview of total activity across all tiers."""
    users = (await list_users())["users"]
    total_messages = sum(u["message_count"] for u in users)
    by_tier = {}
    for u in users:
        t = u["tier"]
        if t not in by_tier:
            by_tier[t] = {"users": 0, "messages": 0}
        by_tier[t]["users"] += 1
        by_tier[t]["messages"] += u["message_count"]
    return {
        "total_users": len(users),
        "total_messages": total_messages,
        "by_tier": by_tier,
    }

class DeviceRequest(BaseModel):
    device_id: str
    tier: str

@router.get("/devices")
async def list_devices() -> dict[str, Any]:
    """List all registered browser devices grouped by tier."""
    registry = _read_registry()
    devices: list[dict[str, str]] = []
    for tier in _ALL_REGISTRY_TIERS:
        for did in registry.get(tier, {}).get("browser_device_ids", []):
            devices.append({"device_id": did, "tier": tier})
    return {"devices": devices}

@router.put("/devices")
async def register_device(req: DeviceRequest) -> dict[str, str]:
    """Register a browser device UUID to a tier (or move it to a new tier)."""
    if req.tier not in set(_ALL_REGISTRY_TIERS):
        raise HTTPException(status_code=400, detail=f"Invalid tier: {req.tier}")

    did = req.device_id.strip()
    if not did:
        raise HTTPException(status_code=400, detail="device_id cannot be empty")

    registry = _read_registry()
    for t in _ALL_REGISTRY_TIERS:
        ids = registry.get(t, {}).get("browser_device_ids", [])
        if did in ids:
            ids.remove(did)
    registry.setdefault(req.tier, {}).setdefault("browser_device_ids", []).append(did)
    _write_registry(registry)

    from app.core.user_registry import user_registry
    user_registry._loaded_at = 0.0

    return {"status": "ok", "device_id": did, "tier": req.tier}

@router.delete("/devices/{device_id}")
async def remove_device(device_id: str) -> dict[str, str]:
    """Remove a registered browser device UUID from all tiers."""
    registry = _read_registry()
    removed = False
    for t in _ALL_REGISTRY_TIERS:
        ids = registry.get(t, {}).get("browser_device_ids", [])
        if device_id in ids:
            ids.remove(device_id)
            removed = True

    if not removed:
        raise HTTPException(status_code=404, detail="Device not found")

    _write_registry(registry)
    from app.core.user_registry import user_registry
    user_registry._loaded_at = 0.0

    return {"status": "ok", "device_id": device_id}