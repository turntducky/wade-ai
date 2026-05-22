import pytest
import tempfile, textwrap

from pathlib import Path

from app.skills.registry import (
    SkillManifest, parse_sidecar, get_tool_risk,
    TOOL_INVENTORY, load_all_skills,
)

def _write_sidecar(tmp_path: Path, name: str, risk: str | None = None) -> Path:
    risk_line = f"risk: {risk}" if risk else ""
    content = textwrap.dedent(f"""\
        ---
        name: {name}
        description: A test skill
        category: test
        {risk_line}
        parameters: {{}}
        required: []
        ---
        # {name}
        Test instructions.
    """)
    p = tmp_path / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return p

def test_sidecar_parses_risk_low(tmp_path):
    p = _write_sidecar(tmp_path, "my_tool", risk="low")
    data = parse_sidecar(p)
    assert data is not None
    assert data["manifest"].risk == "low"

def test_sidecar_parses_risk_high(tmp_path):
    p = _write_sidecar(tmp_path, "my_tool", risk="high")
    data = parse_sidecar(p)
    assert data is not None
    assert data["manifest"].risk == "high"

def test_sidecar_defaults_risk_to_low_when_omitted(tmp_path):
    p = _write_sidecar(tmp_path, "my_tool", risk=None)
    data = parse_sidecar(p)
    assert data is not None
    assert data["manifest"].risk == "low"

def test_skill_manifest_default_risk():
    m = SkillManifest()
    assert m.risk == "low"

def test_get_tool_risk_returns_low_for_unknown():
    assert get_tool_risk("nonexistent_tool_xyz") == "low"