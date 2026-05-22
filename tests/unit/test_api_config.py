import pytest

from fastapi import FastAPI
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.api.v1.config import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)

def test_get_config_returns_assistant_name():
    with patch("app.core.config.ConfigManager.get_assistant_name", return_value="Jarvis"):
        r = client.get("/api/v1/config")
    assert r.status_code == 200
    assert r.json()["assistant_name"] == "Jarvis"

def test_patch_config_updates_name():
    saved = {}
    with patch("app.core.config.ConfigManager.get", return_value={}), \
         patch("app.core.config.ConfigManager.save", side_effect=lambda c: saved.update(c)), \
         patch("app.core.config.ConfigManager.get_assistant_name", return_value="Atlas"):
        r = client.patch("/api/v1/config", json={"assistant_name": "Atlas"})
    assert r.status_code == 200
    assert r.json()["assistant_name"] == "Atlas"

def test_patch_config_rejects_empty_name():
    r = client.patch("/api/v1/config", json={"assistant_name": ""})
    assert r.status_code == 422

def test_patch_config_rejects_name_over_64_chars():
    r = client.patch("/api/v1/config", json={"assistant_name": "A" * 65})
    assert r.status_code == 422
