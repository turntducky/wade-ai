import os
import json
import copy
import threading

from pathlib import Path

CREDENTIALS_FILE = Path.home() / ".wade" / "credentials.json"

class CredentialsManager:
    _cache: dict | None = None
    _lock = threading.RLock()

    @classmethod
    def get(cls, service: str) -> dict | None:
        """Return a deep copy of credentials for the given service, or None."""
        with cls._lock:
            data = cls._load()
            val = data.get(service)
            return copy.deepcopy(val) if val is not None else None

    @classmethod
    def save(cls, service: str, data: dict) -> None:
        """Merge data for service into credentials.json. Atomic write."""
        with cls._lock:
            all_creds = cls._load()
            all_creds[service] = copy.deepcopy(data)
            tmp = CREDENTIALS_FILE.with_suffix(".tmp")
            CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(all_creds, indent=2), encoding="utf-8")
            tmp.replace(CREDENTIALS_FILE)
            cls._cache = all_creds
            try:
                os.chmod(CREDENTIALS_FILE, 0o600)
            except OSError:
                pass

    @classmethod
    def all(cls) -> dict:
        """Return a deep copy of all credentials."""
        with cls._lock:
            return copy.deepcopy(cls._load())

    @classmethod
    def _load(cls) -> dict:
        """Internal: load from disk if cache is empty. Must be called with lock held."""
        cache = cls._cache
        if cache is None:
            if CREDENTIALS_FILE.exists():
                try:
                    cache = json.loads(
                        CREDENTIALS_FILE.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    cache = {}
            else:
                cache = {}
            cls._cache = cache
        return cache