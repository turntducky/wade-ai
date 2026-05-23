from __future__ import annotations

import logging

from typing import Any
from fastapi import APIRouter, Body, HTTPException

from app.core.credentials import CredentialsManager

try:
    from blinkpy.auth import Auth as BlinkAuth
    from blinkpy.blinkpy import Blink
    try:
        from blinkpy.helpers.errors import BlinkTwoFARequiredError as _BlinkTwoFAError  # type: ignore
    except ImportError:
        _BlinkTwoFAError = None
    _BLINK_AVAILABLE = True
except ImportError:
    _BLINK_AVAILABLE = False
    _BlinkTwoFAError = None
    Blink: Any = None
    BlinkAuth: Any = None

logger = logging.getLogger("wade.blink_auth")

router = APIRouter(prefix="/api/blink", tags=["blink"])

_pending_blink: Any = None

def _is_2fa_error(exc: Exception) -> bool:
    return (
        (_BlinkTwoFAError is not None and isinstance(exc, _BlinkTwoFAError))
        or type(exc).__name__ == "BlinkTwoFARequiredError"
    )

@router.get("/status")
async def blink_status() -> dict:
    creds = CredentialsManager.get("blink") or {}
    return {"connected": bool(creds.get("token"))}

@router.post("/login")
async def blink_login() -> dict:
    if not _BLINK_AVAILABLE:
        raise HTTPException(503, "blinkpy not installed — run: pip install blinkpy")

    creds = CredentialsManager.get("blink") or {}
    email    = creds.get("email", "")
    password = creds.get("password", "")
    if not email or not password:
        raise HTTPException(400, "Save your Blink email and password first")

    global _pending_blink

    blink = Blink()
    blink.auth = BlinkAuth({"username": email, "password": password}, no_prompt=True)

    try:
        await blink.start()
        CredentialsManager.save("blink", {**creds, **blink.auth.login_attributes})
        _pending_blink = None
        return {"needs_2fa": False, "status": "connected"}
    except Exception as exc:
        if _is_2fa_error(exc):
            _pending_blink = blink
            return {"needs_2fa": True}
        logger.error("[blink_auth] login failed: %s", exc, exc_info=True)
        raise HTTPException(502, f"Login failed: {exc}") from exc

@router.post("/verify")
async def blink_verify(body: dict = Body(...)) -> dict:
    pin = str(body.get("pin", "")).strip()
    if not pin:
        raise HTTPException(400, "PIN is required")

    global _pending_blink
    if _pending_blink is None:
        raise HTTPException(400, "No pending session — click Login first")

    try:
        await _pending_blink.send_2fa_code(pin)
        creds = CredentialsManager.get("blink") or {}
        CredentialsManager.save("blink", {**creds, **_pending_blink.auth.login_attributes})
        _pending_blink = None
        return {"ok": True}
    except Exception as exc:
        logger.warning("[blink_auth] 2FA verify failed: %s", exc)
        return {"ok": False, "message": str(exc)}

@router.post("/disconnect")
async def blink_disconnect() -> dict:
    creds = CredentialsManager.get("blink") or {}
    CredentialsManager.save("blink", {
        "email":    creds.get("email", ""),
        "password": creds.get("password", ""),
    })

    try:
        import app.skills.cameras.blink as _skill
        _skill._blink_instance = None
    except Exception:
        pass
    return {"ok": True}