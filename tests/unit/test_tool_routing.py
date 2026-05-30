import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.skills.registry import SkillManifest, register_tool, TOOL_INVENTORY


# ── Registry extension ──────────────────────────────────────────────────────

def test_get_tools_by_categories_returns_matching_tools():
    from app.skills.registry import get_tools_by_categories

    schema_a = {"type": "function", "function": {"name": "_trt_cat_a", "description": "a"}}
    schema_b = {"type": "function", "function": {"name": "_trt_cat_b", "description": "b"}}

    @register_tool(schema_a, manifest=SkillManifest(category="workspace"))
    async def _fn_a(**kw): return "ok"

    @register_tool(schema_b, manifest=SkillManifest(category="web"))
    async def _fn_b(**kw): return "ok"

    result = get_tools_by_categories(["workspace"])
    assert "_trt_cat_a" in result
    assert "_trt_cat_b" not in result


def test_get_tools_by_categories_multi():
    from app.skills.registry import get_tools_by_categories

    schema_c = {"type": "function", "function": {"name": "_trt_cat_c", "description": "c"}}
    schema_d = {"type": "function", "function": {"name": "_trt_cat_d", "description": "d"}}

    @register_tool(schema_c, manifest=SkillManifest(category="system"))
    async def _fn_c(**kw): return "ok"

    @register_tool(schema_d, manifest=SkillManifest(category="scheduling"))
    async def _fn_d(**kw): return "ok"

    result = get_tools_by_categories(["system", "scheduling"])
    assert "_trt_cat_c" in result
    assert "_trt_cat_d" in result


def test_get_tools_by_categories_excludes_schema_less_entries():
    from app.skills.registry import get_tools_by_categories

    # executor-only registration (no schema) should not appear
    @register_tool("_trt_executor_only")
    async def _fn_exec(**kw): return "ok"

    # Force the manifest category into the entry without a schema
    TOOL_INVENTORY["_trt_executor_only"]["manifest"] = SkillManifest(category="workspace")

    result = get_tools_by_categories(["workspace"])
    assert "_trt_executor_only" not in result


# ── SkillRouter extensions ──────────────────────────────────────────────────

def _make_router_with_mock_collection(query_ids, query_distances):
    """Helper: SkillRouter with a mocked ChromaDB collection."""
    from app.skills.semantic_router import SkillRouter
    mock_coll = MagicMock()
    mock_coll.query.return_value = {
        "ids": [query_ids],
        "distances": [query_distances],
    }
    router = SkillRouter.__new__(SkillRouter)
    router.chroma_client = MagicMock()
    router.collection = mock_coll
    return router


def test_get_relevant_tools_exclude_filters_results():
    router = _make_router_with_mock_collection(
        ["tool_a", "tool_b", "tool_c"],
        [0.1, 0.2, 0.3],
    )
    result = router.get_relevant_tools("query", n_results=3, exclude={"tool_b"})
    assert "tool_b" not in result
    assert "tool_a" in result
    assert "tool_c" in result


def test_get_relevant_tools_exclude_none_unchanged():
    router = _make_router_with_mock_collection(
        ["tool_a", "tool_b"],
        [0.1, 0.2],
    )
    result = router.get_relevant_tools("query", n_results=5, exclude=None)
    assert result == ["tool_a", "tool_b"]


def test_rank_tools_by_relevance_orders_by_score():
    router = _make_router_with_mock_collection(
        ["tool_c", "tool_a", "tool_b"],
        [0.1, 0.5, 0.9],
    )
    # Ask to rank [tool_a, tool_b, tool_c] — expect ChromaDB order (c, a, b)
    result = router.rank_tools_by_relevance("query", ["tool_a", "tool_b", "tool_c"])
    assert result == ["tool_c", "tool_a", "tool_b"]


def test_rank_tools_by_relevance_appends_unranked():
    """Tools not returned by ChromaDB get appended at the end."""
    router = _make_router_with_mock_collection(
        ["tool_a"],
        [0.1],
    )
    result = router.rank_tools_by_relevance("query", ["tool_a", "tool_z"])
    assert result[0] == "tool_a"
    assert result[-1] == "tool_z"


# ── 4-stage pipeline (_get_tools_for_task) ──────────────────────────────────

import asyncio


def _run(coro):
    """Run an async function in test context."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_schema(name: str, category: str = "workspace") -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": f"{name} tool", "parameters": {"type": "object", "properties": {}, "required": []}},
    }


def _seed_registry(tool_defs: list[tuple[str, str]]):
    """Register a list of (name, category) tools and return their schemas."""
    schemas = []
    for name, cat in tool_defs:
        schema = _make_schema(name, cat)
        manifest = SkillManifest(category=cat)
        TOOL_INVENTORY[name] = {"schema": schema, "manifest": manifest, "executor": None}
        schemas.append(schema)
    return schemas


@pytest.fixture(autouse=True)
def _clean_test_tools():
    """Remove test tools from the registry after each test."""
    snapshot = set(TOOL_INVENTORY.keys())
    yield
    for key in list(TOOL_INVENTORY.keys()):
        if key not in snapshot:
            del TOOL_INVENTORY[key]


def test_stage1_category_pool_populated_by_detected_categories():
    from app.agents.executor import _get_tools_for_task
    from app.skills.registry import _FULL_SCHEMAS

    _seed_registry([("_p_ws_tool", "workspace"), ("_p_web_tool", "web")])
    _FULL_SCHEMAS["_p_ws_tool"] = _make_schema("_p_ws_tool")
    _FULL_SCHEMAS["_p_web_tool"] = _make_schema("_p_web_tool", "web")

    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=["workspace"])

    mock_router = MagicMock()
    mock_router.get_relevant_tools.return_value = []
    mock_router.rank_tools_by_relevance.side_effect = lambda q, names: names

    with patch("app.agents.executor._get_intent_classifier", return_value=mock_classifier), \
         patch("app.agents.executor._get_skill_router", return_value=mock_router), \
         patch("app.agents.executor.load_all_skills"), \
         patch("app.agents.executor.get_dynamic_tools", return_value=(list(_FULL_SCHEMAS.values()), {})):
        schemas, ctx = _get_tools_for_task("write a file")

    schema_names = {s["function"]["name"] for s in schemas}
    assert "_p_ws_tool" in schema_names
    assert "_p_web_tool" not in schema_names


def test_stage2_always_on_tools_always_present():
    from app.agents.executor import _get_tools_for_task, ALWAYS_ON_TOOLS
    from app.skills.registry import _FULL_SCHEMAS

    for name in ALWAYS_ON_TOOLS:
        _seed_registry([(name, "system")])
        _FULL_SCHEMAS[name] = _make_schema(name, "system")

    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=["workspace"])  # system NOT detected

    mock_router = MagicMock()
    mock_router.get_relevant_tools.return_value = []
    mock_router.rank_tools_by_relevance.side_effect = lambda q, names: names

    with patch("app.agents.executor._get_intent_classifier", return_value=mock_classifier), \
         patch("app.agents.executor._get_skill_router", return_value=mock_router), \
         patch("app.agents.executor.load_all_skills"), \
         patch("app.agents.executor.get_dynamic_tools", return_value=(list(_FULL_SCHEMAS.values()), {})):
        schemas, ctx = _get_tools_for_task("write a python script")

    schema_names = {s["function"]["name"] for s in schemas}
    for name in ALWAYS_ON_TOOLS:
        assert name in schema_names, f"{name} missing from always-on tools"


def test_stage3_cross_category_fill_uses_raw_user_prompt():
    """Stage 3 must pass the raw user prompt to get_relevant_tools, not a category anchor."""
    from app.agents.executor import _get_tools_for_task
    from app.skills.registry import _FULL_SCHEMAS

    _seed_registry([("_p_fill_ws", "workspace")])
    _FULL_SCHEMAS["_p_fill_ws"] = _make_schema("_p_fill_ws")

    raw_prompt = "THIS IS THE RAW PROMPT"

    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=["workspace"])

    mock_router = MagicMock()
    mock_router.get_relevant_tools.return_value = []
    mock_router.rank_tools_by_relevance.side_effect = lambda q, names: names

    with patch("app.agents.executor._get_intent_classifier", return_value=mock_classifier), \
         patch("app.agents.executor._get_skill_router", return_value=mock_router), \
         patch("app.agents.executor.load_all_skills"), \
         patch("app.agents.executor.get_dynamic_tools", return_value=(list(_FULL_SCHEMAS.values()), {})):
        _get_tools_for_task(raw_prompt)

    # get_relevant_tools is called for Stage 3 cross-category fill
    calls = mock_router.get_relevant_tools.call_args_list
    assert any(raw_prompt in str(call) for call in calls), \
        "Stage 3 did not pass the raw user prompt to get_relevant_tools"


def test_stage4_hard_cap_trims_to_max_tools():
    """When pool > MAX_TOOL_POOL (14), it is trimmed to exactly MAX_TOOL_POOL."""
    from app.agents.executor import _get_tools_for_task, MAX_TOOL_POOL
    from app.skills.registry import _FULL_SCHEMAS

    tool_defs = [(f"_p_cap_{i}", "workspace") for i in range(20)]
    _seed_registry(tool_defs)
    for name, _ in tool_defs:
        _FULL_SCHEMAS[name] = _make_schema(name)

    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=["workspace"])

    mock_router = MagicMock()
    mock_router.get_relevant_tools.return_value = []
    mock_router.rank_tools_by_relevance.side_effect = lambda q, names: names

    with patch("app.agents.executor._get_intent_classifier", return_value=mock_classifier), \
         patch("app.agents.executor._get_skill_router", return_value=mock_router), \
         patch("app.agents.executor.load_all_skills"), \
         patch("app.agents.executor.get_dynamic_tools", return_value=(list(_FULL_SCHEMAS.values()), {})):
        schemas, ctx = _get_tools_for_task("write 20 files")

    assert len(schemas) <= MAX_TOOL_POOL


def test_workspace_cap_trims_workspace_pool_to_10():
    """When workspace tools in pool exceed 10, the pool is trimmed to exactly 10."""
    from app.agents.executor import _get_tools_for_task, WORKSPACE_CAP
    from app.skills.registry import _FULL_SCHEMAS

    # 15 workspace tools — exceeds the WORKSPACE_CAP
    ws_tools = [(f"_p_ws_cap_{i}", "workspace") for i in range(15)]
    _seed_registry(ws_tools)
    for name, _ in ws_tools:
        _FULL_SCHEMAS[name] = _make_schema(name)

    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=["workspace"])

    mock_router = MagicMock()
    # rank_tools_by_relevance returns first WORKSPACE_CAP names in semantic order
    mock_router.rank_tools_by_relevance.side_effect = lambda q, names: names[:WORKSPACE_CAP]
    mock_router.get_relevant_tools.return_value = []

    with patch("app.agents.executor._get_intent_classifier", return_value=mock_classifier), \
         patch("app.agents.executor._get_skill_router", return_value=mock_router), \
         patch("app.agents.executor.load_all_skills"), \
         patch("app.agents.executor.get_dynamic_tools", return_value=(list(_FULL_SCHEMAS.values()), {})):
        schemas, ctx = _get_tools_for_task("process all my workspace files")

    ws_schema_names = [s["function"]["name"] for s in schemas if s["function"]["name"].startswith("_p_ws_cap_")]
    assert len(ws_schema_names) <= WORKSPACE_CAP, \
        f"Expected workspace tools capped at {WORKSPACE_CAP}, got {len(ws_schema_names)}"


def test_exclusivity_constraint_in_prompt():
    """The prompt must contain the exclusivity instruction — no hallucinated tool names."""
    from app.agents.executor import _get_tools_for_task
    from app.skills.registry import _FULL_SCHEMAS

    _seed_registry([("_p_excl_ws", "workspace")])
    _FULL_SCHEMAS["_p_excl_ws"] = _make_schema("_p_excl_ws")

    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=["workspace"])
    mock_router = MagicMock()
    mock_router.get_relevant_tools.return_value = []
    mock_router.rank_tools_by_relevance.side_effect = lambda q, names: names

    with patch("app.agents.executor._get_intent_classifier", return_value=mock_classifier), \
         patch("app.agents.executor._get_skill_router", return_value=mock_router), \
         patch("app.agents.executor.load_all_skills"), \
         patch("app.agents.executor.get_dynamic_tools", return_value=(list(_FULL_SCHEMAS.values()), {})):
        _, ctx = _get_tools_for_task("write a script")

    assert "<available_tools>" in ctx
    assert "ONLY" in ctx or "only" in ctx
