from __future__ import annotations

import httpx
import base64
import logging
import asyncio

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Body, HTTPException

from app.core.credentials import CredentialsManager

try:
    from blinkpy.blinkpy import Blink
    from blinkpy.auth import Auth as BlinkAuth
    _BLINK_AVAILABLE = True
except ImportError:
    _BLINK_AVAILABLE = False

logger = logging.getLogger("wade.credentials")

router = APIRouter(prefix="/api/credentials", tags=["credentials"])

SERVICE_REGISTRY: dict[str, dict] = {
    "openai": {
        "label": "OpenAI",
        "description": "GPT-4o · Embeddings",
        "group": "llm",
        "fields": [{"key": "api_key", "label": "API Key", "type": "password"}],
    },
    "anthropic": {
        "label": "Anthropic",
        "description": "Claude 3.5 Sonnet",
        "group": "llm",
        "fields": [{"key": "api_key", "label": "API Key", "type": "password"}],
    },
    "gemini": {
        "label": "Gemini",
        "description": "Flash · Pro",
        "group": "llm",
        "fields": [{"key": "api_key", "label": "API Key", "type": "password"}],
    },
    "notion": {
        "label": "Notion",
        "description": "Workspace sync",
        "group": "integrations",
        "fields": [{"key": "token", "label": "Integration Token", "type": "password"}],
    },
    "blink": {
        "label": "Blink",
        "description": "Camera feeds",
        "group": "integrations",
        "fields": [
            {"key": "email",    "label": "Email",    "type": "email"},
            {"key": "password", "label": "Password", "type": "password"},
        ],
    },
    "spotify": {
        "label": "Spotify",
        "description": "Playback · Search · Listening history",
        "group": "integrations",
        "fields": [
            {"key": "client_id",     "label": "Client ID",     "type": "text"},
            {"key": "client_secret", "label": "Client Secret", "type": "password"},
        ],
        "oauth": {
            "auth_url":       "/api/spotify/auth",
            "status_url":     "/api/spotify/status",
            "disconnect_url": "/api/spotify/disconnect",
            "hint": "After saving your Client ID, click 'Connect Spotify' to authorize W.A.D.E. via your Spotify account.",
        },
    },
}

def _is_configured(service: str) -> bool:
    stored = CredentialsManager.get(service) or {}
    required = [f["key"] for f in SERVICE_REGISTRY[service]["fields"]]
    return all(stored.get(k) for k in required)

@router.get("")
async def get_credentials() -> dict:
    return {
        svc: {
            "configured": _is_configured(svc),
            "fields": [f["key"] for f in meta["fields"]],
        }
        for svc, meta in SERVICE_REGISTRY.items()
    }

@router.post("/{service}")
async def save_credentials(service: str, data: dict = Body(...)) -> dict:
    if service not in SERVICE_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service}")
    CredentialsManager.save(service, data)
    return {"ok": True}

@router.delete("/{service}")
async def clear_credentials(service: str) -> dict:
    if service not in SERVICE_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service}")
    CredentialsManager.save(service, {})
    return {"ok": True}

async def _test_openai(creds: dict) -> dict:
    api_key = creds.get("api_key", "")
    if not api_key:
        return {"ok": False, "message": "Not configured"}
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5.0,
            )
            r.raise_for_status()
        return {"ok": True, "message": "Verified"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection timed out"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "message": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}

async def _test_anthropic(creds: dict) -> dict:
    api_key = creds.get("api_key", "")
    if not api_key:
        return {"ok": False, "message": "Not configured"}
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=5.0,
            )
        if r.status_code == 401:
            return {"ok": False, "message": "Invalid API key"}
        return {"ok": True, "message": "Verified"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection timed out"}
    except Exception as e:
        return {"ok": False, "message": str(e)}

async def _test_gemini(creds: dict) -> dict:
    api_key = creds.get("api_key", "")
    if not api_key:
        return {"ok": False, "message": "Not configured"}
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
                timeout=5.0,
            )
            r.raise_for_status()
        return {"ok": True, "message": "Verified"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection timed out"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "message": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}

async def _test_notion(creds: dict) -> dict:
    token = creds.get("token", "")
    if not token:
        return {"ok": False, "message": "Not configured"}
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                "https://api.notion.com/v1/users/me",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": "2022-06-28",
                },
                timeout=5.0,
            )
            r.raise_for_status()
        return {"ok": True, "message": "Verified"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection timed out"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "message": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}

async def _test_blink(creds: dict) -> dict:
    if not _BLINK_AVAILABLE:
        return {"ok": False, "message": "blinkpy not installed — run: pip install blinkpy"}
    email = creds.get("email", "")
    if not email:
        return {"ok": False, "message": "Not configured"}
    if creds.get("token"):
        return {"ok": True, "message": "Authenticated — Blink token present"}
    return {"ok": False, "message": "Credentials saved — use the Login button to complete 2FA"}

async def _test_spotify(creds: dict) -> dict:
    client_id     = creds.get("client_id", "")
    client_secret = creds.get("client_secret", "")
    if not client_id or not client_secret:
        return {"ok": False, "message": "Not configured"}
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                "https://accounts.spotify.com/api/token",
                headers={"Authorization": f"Basic {encoded}"},
                data={"grant_type": "client_credentials"},
                timeout=5.0,
            )
            r.raise_for_status()
        return {"ok": True, "message": "Verified"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection timed out"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "message": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}

_TEST_HANDLERS: dict[str, Callable[[dict], Awaitable[dict]]] = {
    "openai":    _test_openai,
    "anthropic": _test_anthropic,
    "gemini":    _test_gemini,
    "notion":    _test_notion,
    "blink":     _test_blink,
    "spotify":   _test_spotify,
}

@router.post("/{service}/test")
async def test_credentials(service: str) -> dict:
    if service not in SERVICE_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service}")
    handler = _TEST_HANDLERS.get(service)
    if handler is None:
        return {"ok": False, "message": "Test not yet implemented for this service"}
    creds = CredentialsManager.get(service) or {}
    required = [f["key"] for f in SERVICE_REGISTRY[service]["fields"]]
    if not all(creds.get(k) for k in required):
        return {"ok": False, "message": "Not configured — save credentials first"}
    return await handler(creds)
