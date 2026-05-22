from app.skills.registry import (
    SkillManifest, register_tool, get_tools_by_manifest, TOOL_INVENTORY
)

def test_skill_manifest_defaults():
    m = SkillManifest()
    assert m.requires_network is False
    assert m.category == "general"
    assert m.tier == "common"
    assert m.reversible is True

def test_register_tool_stores_manifest():
    schema = {"function": {"name": "_test_manifest_a", "description": "test"}}
    manifest = SkillManifest(requires_network=True, category="web")

    @register_tool(schema, manifest=manifest)
    async def _fn(**kwargs):
        return "ok"

    stored = TOOL_INVENTORY["_test_manifest_a"]["manifest"]
    assert stored.requires_network is True
    assert stored.category == "web"

def test_register_tool_without_manifest_gets_defaults():
    schema = {"function": {"name": "_test_manifest_b", "description": "test"}}

    @register_tool(schema)
    async def _fn2(**kwargs):
        return "ok"

    stored = TOOL_INVENTORY["_test_manifest_b"]["manifest"]
    assert isinstance(stored, SkillManifest)
    assert stored.requires_network is False
    assert stored.tier == "common"

def test_get_tools_by_manifest_excludes_network_when_offline():
    schema = {"function": {"name": "_test_manifest_c", "description": "test"}}

    @register_tool(schema, manifest=SkillManifest(requires_network=True))
    async def _fn3(**kwargs):
        return "ok"

    offline_tools, _ = get_tools_by_manifest(network_available=False)
    online_tools, _ = get_tools_by_manifest(network_available=True)

    online_names = [t["function"]["name"] for t in online_tools]
    offline_names = [t["function"]["name"] for t in offline_tools]

    assert "_test_manifest_c" in online_names
    assert "_test_manifest_c" not in offline_names

def test_get_tools_by_manifest_filters_by_tier():
    schema = {"function": {"name": "_test_manifest_d", "description": "test"}}

    @register_tool(schema, manifest=SkillManifest(tier="session"))
    async def _fn4(**kwargs):
        return "ok"

    common_tools, _ = get_tools_by_manifest(tier="common")
    session_tools, _ = get_tools_by_manifest(tier="session")

    common_names = [t["function"]["name"] for t in common_tools]
    session_names = [t["function"]["name"] for t in session_tools]

    assert "_test_manifest_d" not in common_names
    assert "_test_manifest_d" in session_names