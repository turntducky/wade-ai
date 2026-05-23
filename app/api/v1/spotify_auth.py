from __future__ import annotations

import os
import time
import httpx
import base64
import hashlib
import logging

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import ConfigManager
from app.core.credentials import CredentialsManager

logger = logging.getLogger("wade.spotify.auth")

router = APIRouter(prefix="/api/spotify", tags=["spotify"])

SPOTIFY_AUTH_URL  = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

REQUIRED_SCOPES = " ".join([
    "user-read-playback-state",
    "user-read-currently-playing",
    "user-modify-playback-state",
    "user-read-recently-played",
    "user-top-read",
    "user-library-read",
])

_pkce_store: dict[str, tuple[str, float]] = {}
_PKCE_TTL = 600  # seconds

def _purge_expired_pkce() -> None:
    now = time.time()
    expired = [k for k, (_, ts) in _pkce_store.items() if now - ts > _PKCE_TTL]
    for k in expired:
        _pkce_store.pop(k, None)

def _generate_code_verifier() -> str:
    """43–128 char URL-safe random string as per the PKCE spec."""
    return base64.urlsafe_b64encode(os.urandom(64)).rstrip(b"=").decode()

def _generate_code_challenge(verifier: str) -> str:
    """SHA-256 hash of verifier, base64url-encoded without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

def _redirect_uri() -> str:
    port = ConfigManager.get().get("port", 8000)
    return f"http://127.0.0.1:{port}/api/spotify/callback"

@router.get("/status")
async def spotify_status() -> dict:
    """Return whether Spotify is configured and authorized."""
    creds = CredentialsManager.get("spotify") or {}
    configured   = bool(creds.get("client_id"))
    has_tokens   = bool(creds.get("access_token") and creds.get("refresh_token"))
    expires_at   = float(creds.get("token_expires_at", 0))
    token_valid  = has_tokens and time.time() < expires_at
    return {
        "configured":    configured,
        "authorized":    token_valid,
        "has_tokens":    has_tokens,
        "token_expired": has_tokens and not token_valid,
        "redirect_uri":  _redirect_uri(),
    }

@router.get("/auth")
async def spotify_auth():
    """Initiate the PKCE authorization flow. Redirects the browser to Spotify's consent screen."""
    creds     = CredentialsManager.get("spotify") or {}
    client_id = creds.get("client_id", "").strip()
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="Spotify client_id is not configured. Save your credentials in the Credentials tab first.",
        )

    _purge_expired_pkce()

    verifier  = _generate_code_verifier()
    challenge = _generate_code_challenge(verifier)
    state     = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    _pkce_store[state] = (verifier, time.time())

    params = {
        "client_id":             client_id,
        "response_type":         "code",
        "redirect_uri":          _redirect_uri(),
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
        "scope":                 REQUIRED_SCOPES,
        "state":                 state,
    }
    return RedirectResponse(f"{SPOTIFY_AUTH_URL}?{urlencode(params)}")

@router.get("/callback")
async def spotify_callback(
    code:  str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> HTMLResponse:
    """Handle Spotify's redirect after the user grants/denies access."""
    if error:
        logger.warning("[SPOTIFY] Authorization denied by user: %s", error)
        return _result_page(success=False, message=f"Authorization denied: {error}")

    if not code or not state:
        return _result_page(success=False, message="Missing code or state in callback.")

    entry = _pkce_store.pop(state, None)
    if entry is None:
        return _result_page(success=False, message="Invalid or expired state parameter. Please try authorizing again.")

    verifier, created_at = entry
    if time.time() - created_at > _PKCE_TTL:
        return _result_page(success=False, message="Authorization session timed out. Please try again.")

    creds     = CredentialsManager.get("spotify") or {}
    client_id = creds.get("client_id", "").strip()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SPOTIFY_TOKEN_URL,
                data={
                    "grant_type":    "authorization_code",
                    "code":          code,
                    "redirect_uri":  _redirect_uri(),
                    "client_id":     client_id,
                    "code_verifier": verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15.0,
            )
    except httpx.RequestError as exc:
        logger.error("[SPOTIFY] Token exchange network error: %s", exc)
        return _result_page(success=False, message="Network error during token exchange. Check your internet connection.")

    if resp.status_code != 200:
        logger.error("[SPOTIFY] Token exchange failed %d: %s", resp.status_code, resp.text[:300])
        return _result_page(success=False, message=f"Token exchange failed (HTTP {resp.status_code}). Check your Client ID.")

    tokens = resp.json()
    CredentialsManager.save("spotify", {
        **creds,
        "access_token":     tokens["access_token"],
        "refresh_token":    tokens.get("refresh_token") or creds.get("refresh_token", ""),
        "token_expires_at": time.time() + tokens.get("expires_in", 3600) - 60,
        "token_scope":      tokens.get("scope", ""),
    })

    logger.info("[SPOTIFY] Authorization complete. Scopes granted: %s", tokens.get("scope", ""))
    return _result_page(success=True, message="Spotify connected successfully! You can close this tab.")

@router.post("/disconnect")
async def spotify_disconnect() -> dict:
    """Revoke stored tokens while preserving client_id and client_secret."""
    creds = CredentialsManager.get("spotify") or {}
    CredentialsManager.save("spotify", {
        "client_id":     creds.get("client_id", ""),
        "client_secret": creds.get("client_secret", ""),
    })
    logger.info("[SPOTIFY] Tokens cleared.")
    return {"ok": True}

def _result_page(*, success: bool, message: str) -> HTMLResponse:
    """Minimal self-closing HTML page shown after the OAuth callback."""
    icon    = "✅" if success else "❌"
    color   = "#1DB954" if success else "#e74c3c"
    port    = ConfigManager.get().get("port", 8000)
    ui_url  = f"http://127.0.0.1:{port}/ui"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Spotify — W.A.D.E.</title>
  <style>
    body {{ font-family: monospace; background: #0f0f0f; color: #eee;
           display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .card {{ text-align: center; padding: 2rem 3rem; border: 1px solid {color};
             border-radius: 8px; max-width: 420px; }}
    h2 {{ color: {color}; margin-bottom: .5rem; }}
    a {{ color: #1DB954; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>{icon} {message}</h2>
    <p><a href="{ui_url}">← Return to W.A.D.E.</a></p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)