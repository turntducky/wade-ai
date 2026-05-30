import pytest
from unittest.mock import MagicMock, patch

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
