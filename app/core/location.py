import time
import httpx
import logging

from datetime import datetime

logger = logging.getLogger("wade.location")

_cached_location = None
_cached_timezone = None
_cache_expiry: float = 0.0
_CACHE_TTL = 6 * 3600

def get_system_location() -> tuple[str, str]:
    """Get the system's current location and timezone. Uses caching to avoid repeated lookups."""
    global _cached_location, _cached_timezone, _cache_expiry

    now = time.monotonic()
    if _cached_location is not None and _cached_timezone is not None and now < _cache_expiry:
        return _cached_location, _cached_timezone

    _cached_timezone = datetime.now().astimezone().tzname() or "Local Time"
    _cached_location = "Unknown Location (Offline)"

    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get("https://ipinfo.io/json")
            if response.status_code == 200:
                data = response.json()
                city = data.get("city", "")
                region = data.get("region", "")
                country = data.get("country", "")
                _cached_location = f"{city}, {region}, {country}".strip(", ")
                _cached_timezone = data.get("timezone", _cached_timezone)

    except Exception as e:
        logger.warning(f"[LOCATION] Failed to fetch IP geolocation, falling back to OS defaults: {e}")

    _cache_expiry = time.monotonic() + _CACHE_TTL
    return _cached_location, _cached_timezone