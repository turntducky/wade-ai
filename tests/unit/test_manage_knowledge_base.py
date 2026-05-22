import pytest


from pathlib import Path
from unittest.mock import patch

from app.memory.passive_extractor import extract_and_store
from app.skills.memory.updater import manage_knowledge_base

async def test_extract_and_store_patches_user_md(tmp_path):
    user_md = tmp_path / "USER.md"
    user_md.write_text("- **Name:** Unknown\n", encoding="utf-8")

    with patch("app.memory.passive_extractor._direct_store_fact", return_value=True) as mock_store, \
         patch("app.memory.md_patcher.WORKSPACE_DIR", tmp_path):
        await extract_and_store("Just so you know, my name is Ethan.")

    mock_store.assert_called_once()
    assert "- **Name:** Ethan" in user_md.read_text(encoding="utf-8")

async def test_patch_field_replaces_sentinel(tmp_path):
    f = tmp_path / "NOTES.md"
    f.write_text("<!-- Company/Brand Name -->", encoding="utf-8")
    with patch("app.skills.memory.updater.WORKSPACE_DIR", tmp_path):
        result = await manage_knowledge_base(
            action="patch_field",
            target="NOTES.md",
            sentinel="<!-- Company/Brand Name -->",
            content="Acme Corp",
        )
    assert "successfully" in result
    assert "Acme Corp" in f.read_text(encoding="utf-8")

async def test_patch_field_returns_not_found_when_sentinel_missing(tmp_path):
    f = tmp_path / "NOTES.md"
    f.write_text("no placeholder here", encoding="utf-8")
    with patch("app.skills.memory.updater.WORKSPACE_DIR", tmp_path):
        result = await manage_knowledge_base(
            action="patch_field",
            target="NOTES.md",
            sentinel="<!-- Company/Brand Name -->",
            content="Acme Corp",
        )
    assert "not found" in result.lower()

async def test_patch_field_rejects_protected_file():
    result = await manage_knowledge_base(
        action="patch_field",
        target="BOOTSTRAP.md",
        sentinel="anything",
        content="anything",
    )
    assert "system-internal" in result.lower() or "protected" in result.lower()

async def test_patch_field_requires_sentinel(tmp_path):
    (tmp_path / "NOTES.md").write_text("sentinel here", encoding="utf-8")
    with patch("app.skills.memory.updater.WORKSPACE_DIR", tmp_path):
        result = await manage_knowledge_base(
            action="patch_field",
            target="NOTES.md",
            sentinel="",
            content="value",
        )
    assert "sentinel" in result.lower() and "required" in result.lower()

async def test_patch_field_requires_content(tmp_path):
    (tmp_path / "NOTES.md").write_text("sentinel here", encoding="utf-8")
    with patch("app.skills.memory.updater.WORKSPACE_DIR", tmp_path):
        result = await manage_knowledge_base(
            action="patch_field",
            target="NOTES.md",
            sentinel="something",
            content="",
        )
    assert "content" in result.lower() and "required" in result.lower()