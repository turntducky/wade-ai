from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from app.core.config import ConfigManager

router = APIRouter(prefix="/api/v1/config", tags=["config"])

class ConfigPatch(BaseModel):
    assistant_name: str | None = None

    @field_validator("assistant_name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("assistant_name cannot be empty")
            if len(v) > 64:
                raise ValueError("assistant_name must be 64 characters or fewer")
        return v

@router.get("")
def get_config():
    return {"assistant_name": ConfigManager.get_assistant_name()}

@router.patch("")
def patch_config(body: ConfigPatch):
    config = ConfigManager.get()
    if body.assistant_name is not None:
        config["assistant_name"] = body.assistant_name
        ConfigManager.save(config)
    return {"assistant_name": ConfigManager.get_assistant_name()}
