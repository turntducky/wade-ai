import json
import pytest

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

def test_blink_uses_credentials_manager_when_available(tmp_path):
    """CredentialsManager.get('blink') is called when no old file exists."""
    import importlib
    import app.core.credentials as creds_mod
    creds_mod.CredentialsManager._cache = None
    creds_mod.CREDENTIALS_FILE = tmp_path / "credentials.json"
    creds_mod.CredentialsManager.save("blink", {"username": "u@test.com", "password": "pw"})

    with patch("app.skills.cameras.blink.Blink") as mock_blink_cls, \
         patch("app.skills.cameras.blink.Auth") as mock_auth_cls:
        mock_blink = MagicMock()
        mock_blink.start = AsyncMock(return_value=None)
        mock_blink_cls.return_value = mock_blink

        import app.skills.cameras.blink as blink_mod
        blink_mod._blink_instance = None
        blink_mod._migration_done = True

        import asyncio
        asyncio.run(blink_mod._get_blink_instance())

        called_with = mock_auth_cls.call_args[0][0]
        assert called_with["username"] == "u@test.com"

def test_legacy_blink_file_is_migrated(tmp_path):
    """Old blink_credentials.json is copied into credentials.json on first load."""
    old_creds = {"username": "legacy@test.com", "password": "legacy_pw"}
    old_file = tmp_path / "blink_credentials.json"
    old_file.write_text(json.dumps(old_creds))

    import app.core.credentials as creds_mod
    creds_mod.CredentialsManager._cache = None
    creds_mod.CREDENTIALS_FILE = tmp_path / "credentials.json"

    import app.skills.cameras.blink as blink_mod
    with patch("app.skills.cameras.blink._OLD_CREDENTIALS_FILE", old_file):
        creds_mod.CredentialsManager._cache = None
        blink_mod._migrate_legacy_blink_credentials()

    result = creds_mod.CredentialsManager.get("blink")
    assert result is not None
    assert result["username"] == "legacy@test.com"
    assert old_file.exists()