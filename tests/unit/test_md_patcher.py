from pathlib import Path
from unittest.mock import patch

from app.memory.md_patcher import PATCH_MAP, _patch_md_field, patch_if_mapped

def test_patch_md_field_replaces_sentinel(tmp_path):
    f = tmp_path / "USER.md"
    f.write_text("line1\n- **Name:** Unknown\nline3\n", encoding="utf-8")
    result = _patch_md_field(f, "- **Name:** Unknown", "- **Name:** Alex")
    assert result is True
    text = f.read_text(encoding="utf-8")
    assert "- **Name:** Alex" in text
    assert "Unknown" not in text

def test_patch_md_field_noop_when_sentinel_missing(tmp_path):
    f = tmp_path / "USER.md"
    original = "- **Name:** Alex\n"
    f.write_text(original, encoding="utf-8")
    result = _patch_md_field(f, "- **Name:** Unknown", "- **Name:** Bob")
    assert result is False
    assert f.read_text(encoding="utf-8") == original

def test_patch_md_field_noop_when_file_missing(tmp_path):
    result = _patch_md_field(tmp_path / "MISSING.md", "sentinel", "replacement")
    assert result is False

def test_patch_md_field_does_not_propagate_exceptions(tmp_path):
    f = tmp_path / "USER.md"
    f.write_text("sentinel here", encoding="utf-8")
    with patch.object(Path, "write_text", side_effect=OSError("disk full")):
        result = _patch_md_field(f, "sentinel here", "replacement")
    assert result is False

def test_patch_if_mapped_patches_known_key(tmp_path):
    (tmp_path / "USER.md").write_text("- **Name:** Unknown\n", encoding="utf-8")
    with patch("app.memory.md_patcher.WORKSPACE_DIR", tmp_path):
        patch_if_mapped("User: Name", "Alex")
    assert "- **Name:** Alex" in (tmp_path / "USER.md").read_text(encoding="utf-8")

def test_patch_if_mapped_noop_for_unknown_key(tmp_path):
    f = tmp_path / "USER.md"
    f.write_text("- **Name:** Unknown\n", encoding="utf-8")
    original = f.read_text(encoding="utf-8")
    with patch("app.memory.md_patcher.WORKSPACE_DIR", tmp_path):
        patch_if_mapped("User: Likes", "Pizza")
    assert f.read_text(encoding="utf-8") == original

def test_patch_map_covers_all_user_fields():
    expected_keys = {
        "User: Name", "User: Nickname", "User: Age", "User: Birthday",
        "User: Occupation", "User: Location", "User: Timezone",
    }
    assert expected_keys == set(PATCH_MAP.keys())