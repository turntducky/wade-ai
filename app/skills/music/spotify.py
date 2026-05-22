from __future__ import annotations

import time
import httpx
import asyncio
import logging

from typing import Optional

from app.skills.registry import register_tool
from app.core.credentials import CredentialsManager

logger = logging.getLogger("wade.skills.spotify")

SPOTIFY_API_BASE  = "https://api.spotify.com/v1"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_MAX_RETRIES      = 3

class _SpotifyClient:
    """Async wrapper around the Spotify Web API with token refresh and 429 backoff."""

    async def _get_valid_token(self) -> str:
        creds         = CredentialsManager.get("spotify") or {}
        access_token  = creds.get("access_token", "")
        refresh_token = creds.get("refresh_token", "")
        expires_at    = float(creds.get("token_expires_at", 0))

        if not access_token or not refresh_token:
            raise RuntimeError(
                "Spotify is not authorized. Open the Credentials tab and click "
                "'Connect Spotify' to complete the authorization flow."
            )

        if time.time() >= expires_at:
            access_token = await self._refresh_access_token(creds, refresh_token)

        return access_token

    async def _refresh_access_token(self, creds: dict, refresh_token: str) -> str:
        """Refresh using PKCE flow — only client_id in body, no Basic auth header."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SPOTIFY_TOKEN_URL,
                data={
                    "grant_type":    "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id":     creds.get("client_id", ""),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15.0,
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Token refresh failed ({resp.status_code}). "
                "Re-authorize Spotify in the Credentials tab."
            )

        tokens    = resp.json()
        new_creds = {
            **creds,
            "access_token":     tokens["access_token"],
            "token_expires_at": time.time() + tokens.get("expires_in", 3600) - 60,
        }
        if tokens.get("refresh_token"):
            new_creds["refresh_token"] = tokens["refresh_token"]

        CredentialsManager.save("spotify", new_creds)
        logger.debug("[SPOTIFY] Access token refreshed.")
        return tokens["access_token"]

    async def request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,) -> dict | None:
        """Authenticated API call with exponential backoff on HTTP 429."""
        token = await self._get_valid_token()
        url   = f"{SPOTIFY_API_BASE}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        async with httpx.AsyncClient() as client:
            for attempt in range(_MAX_RETRIES):
                resp = await client.request(method, url, params=clean_params or None, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=15.0)

                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                    logger.warning("[SPOTIFY] Rate limited — waiting %ds (attempt %d/%d).", wait, attempt + 1, _MAX_RETRIES)
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code == 204:
                    return None
                if resp.status_code == 401 and attempt == 0:
                    creds = CredentialsManager.get("spotify") or {}
                    creds["token_expires_at"] = 0
                    token = await self._refresh_access_token(creds, creds.get("refresh_token", ""))
                    continue

                if resp.status_code == 403:
                    msg = _error_message(resp)
                    raise RuntimeError(
                        f"Spotify: access denied — {msg}. "
                        "This feature may require a Spotify Premium account."
                    )

                if not resp.is_success:
                    msg = _error_message(resp)
                    raise RuntimeError(f"Spotify API error {resp.status_code}: {msg}")

                return resp.json() if resp.content else None

        raise RuntimeError("Spotify API: request failed after maximum retries.")


_client = _SpotifyClient()

@register_tool("manage_spotify")
async def manage_spotify(action: str, query: Optional[str] = None, uri: Optional[str] = None, value: Optional[str] = None, device_id: Optional[str] = None, time_range: Optional[str] = None, limit: Optional[int] = None) -> str:
    """
    Controls Spotify playback and retrieves listening data via the Web API.

    Actions:
      play, pause, play_pause, next, previous — transport
      seek            — seek to position; value = milliseconds
      volume          — set volume; value = 0–100
      shuffle         — toggle or set; value = "on"/"off" (optional, toggles if omitted)
      repeat          — set mode; value = "track" | "context" | "off"
      search          — search; query required
      search_and_play — search and play top result; query required
      now_playing     — current playback state
      recently_played — recent history
      top_tracks      — most-played tracks (time_range: short_term/medium_term/long_term)
      top_artists     — most-played artists
      queue           — view upcoming queue
      add_to_queue    — add track by uri
      devices         — list Spotify Connect devices
    """
    try:
        return await _dispatch(action, query=query, uri=uri, value=value, device_id=device_id, time_range=time_range, limit=limit or 5)
    except RuntimeError as exc:
        return f"❌ {exc}"
    except Exception as exc:
        logger.exception("[SPOTIFY] Unexpected error for action=%s", action)
        return f"❌ Unexpected error: {exc}"

async def _dispatch(action: str, query: str | None, uri: str | None, value: str | None, device_id: str | None, time_range: str | None, limit: int) -> str:

    if action == "play":
        return await _play(uri=uri, device_id=device_id)

    if action == "pause":
        await _client.request("PUT", "/me/player/pause", params={"device_id": device_id})
        return "⏸ Playback paused."

    if action == "play_pause":
        state = await _client.request("GET", "/me/player")
        if state and state.get("is_playing"):
            await _client.request("PUT", "/me/player/pause")
            return "⏸ Playback paused."
        else:
            await _client.request("PUT", "/me/player/play", body={})
            return "▶ Playback resumed."

    if action == "next":
        await _client.request("POST", "/me/player/next", params={"device_id": device_id})
        return "⏭ Skipped to next track."

    if action == "previous":
        await _client.request("POST", "/me/player/previous", params={"device_id": device_id})
        return "⏮ Skipped to previous track."

    if action == "seek":
        if not value:
            return "❌ 'seek' requires value = position in milliseconds."
        try:
            pos_ms = int(value)
            if pos_ms < 0:
                raise ValueError
        except ValueError:
            return "❌ value must be a non-negative integer (milliseconds)."
        await _client.request("PUT", "/me/player/seek", params={"position_ms": pos_ms, "device_id": device_id})
        return f"⏩ Seeked to {_fmt_ms(pos_ms)}."

    if action == "volume":
        if not value:
            return "❌ 'volume' requires value = integer 0–100."
        try:
            vol = max(0, min(100, int(value)))
        except ValueError:
            return "❌ value must be an integer between 0 and 100."
        await _client.request("PUT", "/me/player/volume", params={"volume_percent": vol, "device_id": device_id})
        return f"🔊 Volume set to {vol}%."

    if action == "shuffle":
        if value is None or value.lower() == "toggle":
            state = await _client.request("GET", "/me/player")
            new_state = not (state.get("shuffle_state", False) if state else False)
        elif value.lower() in ("on", "true", "1", "yes"):
            new_state = True
        else:
            new_state = False
        await _client.request(
            "PUT", "/me/player/shuffle",
            params={"state": str(new_state).lower(), "device_id": device_id},
        )
        return f"🔀 Shuffle {'enabled' if new_state else 'disabled'}."

    if action == "repeat":
        mode = (value or "").lower()
        if mode not in ("track", "context", "off"):
            return "❌ 'repeat' value must be: track, context, or off."
        await _client.request("PUT", "/me/player/repeat", params={"state": mode, "device_id": device_id})
        label = {"track": "🔂 Repeating current track.", "context": "🔁 Repeating context.", "off": "Repeat off."}
        return label[mode]

    if action == "search":
        if not query:
            return "❌ 'search' requires a query."
        return await _search(query, limit=limit)

    if action == "search_and_play":
        if not query:
            return "❌ 'search_and_play' requires a query."
        return await _search_and_play(query, device_id=device_id)

    if action == "now_playing":
        return await _now_playing()

    if action == "recently_played":
        return await _recently_played(limit=min(limit, 50))

    if action == "queue":
        return await _get_queue()

    if action == "add_to_queue":
        if not uri:
            return "❌ 'add_to_queue' requires a Spotify URI (e.g. spotify:track:...)."
        await _client.request("POST", "/me/player/queue", params={"uri": uri, "device_id": device_id})
        return f"➕ Added to queue: {uri}"

    if action == "top_tracks":
        tr = time_range if time_range in ("short_term", "medium_term", "long_term") else "medium_term"
        return await _top_items("tracks", time_range=tr, limit=limit)

    if action == "top_artists":
        tr = time_range if time_range in ("short_term", "medium_term", "long_term") else "medium_term"
        return await _top_items("artists", time_range=tr, limit=limit)

    if action == "devices":
        return await _list_devices()

    return (
        f"❌ Unknown action '{action}'. Valid actions: play, pause, play_pause, next, previous, "
        "seek, volume, shuffle, repeat, search, search_and_play, now_playing, recently_played, "
        "top_tracks, top_artists, queue, add_to_queue, devices."
    )


async def _play(uri: str | None, device_id: str | None) -> str:
    """Start or resume playback. If uri is a context (album/artist/playlist), use context_uri.
    If uri is a track, wrap it in uris[]. If no uri, resume current playback."""
    body: dict = {}
    if uri:
        if any(uri.startswith(f"spotify:{t}:") for t in ("album", "artist", "playlist")):
            body["context_uri"] = uri
        else:
            body["uris"] = [uri]

    await _client.request("PUT", "/me/player/play", params={"device_id": device_id}, body=body)
    return "▶ Playback started." if uri else "▶ Playback resumed."

async def _now_playing() -> str:
    data = await _client.request("GET", "/me/player")
    if not data:
        return "Nothing is currently playing on Spotify."

    item = data.get("item")
    if not item:
        playing_type = data.get("currently_playing_type", "unknown")
        return f"Nothing identifiable is playing (type: {playing_type})."

    is_playing  = data.get("is_playing", False)
    progress_ms = data.get("progress_ms") or 0
    duration_ms = item.get("duration_ms") or 0
    device      = data.get("device") or {}
    repeat      = data.get("repeat_state", "off")
    shuffle     = data.get("shuffle_state", False)

    status = "▶" if is_playing else "⏸"
    flags  = []
    if shuffle:
        flags.append("🔀 Shuffle")
    if repeat == "track":
        flags.append("🔂 Repeat track")
    elif repeat == "context":
        flags.append("🔁 Repeat context")

    lines = [
        f"{status} {_fmt_track(item)}",
        f"   {_fmt_ms(progress_ms)} / {_fmt_ms(duration_ms)}",
        f"   Device: {device.get('name', '?')} ({device.get('type', '?')}) · Vol: {device.get('volume_percent', '?')}%",
    ]
    if flags:
        lines.append(f"   {' · '.join(flags)}")
    return "\n".join(lines)

async def _search(query: str, limit: int) -> str:
    data = await _client.request(
        "GET", "/search",
        params={"q": query, "type": "track,artist,album", "limit": limit},
    )
    if not data:
        return f"No results found for '{query}'."

    lines: list[str] = []

    tracks = (data.get("tracks") or {}).get("items") or []
    if tracks:
        lines.append("🎵 Tracks:")
        for t in tracks:
            lines.append(f"  • {_fmt_track(t)}  [{t.get('uri', '')}]")

    artists = (data.get("artists") or {}).get("items") or []
    if artists:
        lines.append("🎤 Artists:")
        for a in artists[:3]:
            lines.append(f"  • {a.get('name', '?')}  [{a.get('uri', '')}]")

    albums = (data.get("albums") or {}).get("items") or []
    if albums:
        lines.append("💿 Albums:")
        for al in albums[:3]:
            artist_str = ", ".join(a["name"] for a in al.get("artists", []))
            lines.append(f"  • '{al.get('name', '?')}' by {artist_str}  [{al.get('uri', '')}]")

    return "\n".join(lines) if lines else f"No results found for '{query}'."

async def _search_and_play(query: str, device_id: str | None) -> str:
    data = await _client.request(
        "GET", "/search",
        params={"q": query, "type": "track", "limit": 1},
    )
    tracks = ((data or {}).get("tracks") or {}).get("items") or []
    if not tracks:
        return f"❌ No tracks found for '{query}'."

    track = tracks[0]
    uri   = track.get("uri", "")
    await _play(uri=uri, device_id=device_id)
    return f"▶ Now playing: {_fmt_track(track)}"

async def _recently_played(limit: int) -> str:
    data  = await _client.request("GET", "/me/player/recently-played", params={"limit": limit})
    items = (data or {}).get("items") or []
    if not items:
        return "No recently played tracks found."

    lines = ["🕐 Recently played:"]
    for item in items:
        track     = item.get("track") or {}
        played_at = (item.get("played_at") or "")[:16].replace("T", " ")
        lines.append(f"  • {_fmt_track(track)}  ({played_at} UTC)")
    return "\n".join(lines)

async def _top_items(item_type: str, time_range: str, limit: int) -> str:
    data  = await _client.request(
        "GET", f"/me/top/{item_type}",
        params={"time_range": time_range, "limit": min(limit, 50)},
    )
    items = (data or {}).get("items") or []
    if not items:
        return f"No top {item_type} found for '{time_range}'."

    range_labels = {
        "short_term":  "last 4 weeks",
        "medium_term": "last 6 months",
        "long_term":   "all time",
    }
    label = range_labels.get(time_range, time_range)
    emoji = "🎵" if item_type == "tracks" else "🎤"
    lines = [f"{emoji} Your top {item_type} ({label}):"]

    for i, item in enumerate(items, 1):
        if item_type == "tracks":
            lines.append(f"  {i:2d}. {_fmt_track(item)}")
        else:
            genres = ", ".join(item.get("genres", [])[:2])
            suffix = f" — {genres}" if genres else ""
            lines.append(f"  {i:2d}. {item.get('name', '?')}{suffix}")

    return "\n".join(lines)

async def _get_queue() -> str:
    data    = await _client.request("GET", "/me/player/queue")
    current = (data or {}).get("currently_playing")
    queue   = (data or {}).get("queue") or []

    lines: list[str] = []
    if current:
        lines.append(f"▶ Now: {_fmt_track(current)}")
    if queue:
        lines.append("📋 Up next:")
        for i, item in enumerate(queue[:10], 1):
            lines.append(f"  {i}. {_fmt_track(item)}")

    return "\n".join(lines) if lines else "Queue is empty."

async def _list_devices() -> str:
    data    = await _client.request("GET", "/me/player/devices")
    devices = (data or {}).get("devices") or []
    if not devices:
        return (
            "No Spotify devices found. Open Spotify on a phone, computer, or speaker, "
            "then try again."
        )

    lines = ["📱 Available Spotify devices:"]
    for d in devices:
        active     = " ← active" if d.get("is_active") else ""
        restricted = " (restricted)" if d.get("is_restricted") else ""
        vol        = d.get("volume_percent")
        vol_str    = f" · {vol}%" if vol is not None else ""
        lines.append(
            f"  • {d.get('name', '?')} [{d.get('type', '?')}]{vol_str}"
            f"{active}{restricted}  id={d.get('id', '?')}"
        )
    return "\n".join(lines)

def _fmt_track(item: dict) -> str:
    """Format a track or episode object as 'Name by Artist'."""
    name    = item.get("name", "?")
    artists = item.get("artists") or []
    if artists:
        artist_str = ", ".join(a.get("name", "?") for a in artists)
        return f"'{name}' by {artist_str}"
    show = (item.get("show") or {}).get("name", "")
    return f"'{name}'" + (f" ({show})" if show else "")

def _fmt_ms(ms: int) -> str:
    """Format milliseconds as M:SS."""
    total_s = ms // 1000
    return f"{total_s // 60}:{total_s % 60:02d}"

def _error_message(resp: httpx.Response) -> str:
    """Extract the error message from a Spotify error response body."""
    try:
        body = resp.json()
        return body.get("error", {}).get("message", resp.text)
    except Exception:
        return resp.text[:200]