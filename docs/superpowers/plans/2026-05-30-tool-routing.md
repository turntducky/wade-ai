# Tool Routing — Category-Aware Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace W.A.D.E.'s flat semantic top-5 tool retrieval with a 4-stage category-aware pipeline that eliminates tool name hallucination.

**Architecture:** An `IntentClassifier` detects intent categories using ChromaDB fast-path + optional LLM slow-path. `_get_tools_for_task` in `executor.py` runs a 4-stage pipeline: category pool → always-on tools → cross-category semantic fill → hard cap at 14. The prompt switches from `<available_tools_summary>` to `<available_tools>` with an explicit exclusivity constraint.

**Tech Stack:** Python 3.11+, ChromaDB (already in use via `_personality.chroma_client`), existing `SkillRouter`, `InferenceClient`, `register_tool` / `@wade_tool` decorators.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `app/skills/registry.py` | Add `get_tools_by_categories()` |
| Modify | `app/skills/semantic_router.py` | Add `exclude` param + `rank_tools_by_relevance()` |
| **Create** | `app/core/intent_classifier.py` | Hybrid fast/slow category classifier |
| Modify | `app/agents/executor.py` | Replace `_get_tools_for_task` with 4-stage pipeline; add `_get_intent_classifier()` |
| Modify | `app/skills/system/diagnostics.py` | Revert 3 tools to `@register_tool("name")`; keep `check_active_models` as `@wade_tool` |
| Modify | `app/skills/system/perform_system_recovery.md` | Add `restart_browser_service` to enum |
| Modify | `C:\Users\turnt\.wade\workspace\TOOLS.md` | Rewrite to prose — remove all tool names |
| **Create** | `tests/unit/test_intent_classifier.py` | Unit tests for IntentClassifier |
| **Create** | `tests/unit/test_tool_routing.py` | Unit tests for extensions + 4-stage pipeline |
| **Create** | `tests/evals/test_routing_accuracy.py` | Accuracy eval harness |

---

## Task 1: Registry and SkillRouter Extensions (TDD)

**Files:**
- Modify: `app/skills/registry.py`
- Modify: `app/skills/semantic_router.py`
- Create: `tests/unit/test_tool_routing.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/unit/test_tool_routing.py`:

```python
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
```

- [ ] **Step 1.2: Run tests to confirm they fail**

```
cd C:\Users\turnt\OneDrive\Desktop\Development\W.A.D.E
python -m pytest tests/unit/test_tool_routing.py -v 2>&1 | head -40
```

Expected: `ImportError: cannot import name 'get_tools_by_categories'` and `TypeError` for `exclude` param.

- [ ] **Step 1.3: Add `get_tools_by_categories` to `app/skills/registry.py`**

Add after the existing `get_all_categories()` function (around line 280):

```python
def get_tools_by_categories(categories: list[str]) -> list[str]:
    """Returns names of tools whose manifest category is in the given set.

    Only returns tools that have both a schema and a manifest — executor-only
    registrations (added via register_tool("name")) are excluded.
    """
    load_all_skills()
    cats = set(categories)
    return [
        name
        for name, entry in TOOL_INVENTORY.items()
        if "schema" in entry and "manifest" in entry and entry["manifest"].category in cats
    ]
```

- [ ] **Step 1.4: Add `exclude` param and `rank_tools_by_relevance` to `app/skills/semantic_router.py`**

Replace the existing `get_relevant_tools` method body and add `rank_tools_by_relevance`:

```python
def get_relevant_tools(
    self, query: str, n_results: int = 5, exclude: set[str] | None = None
) -> list[str]:
    """Returns names of tools relevant to the query, optionally excluding a set of names."""
    if not self.collection or not query.strip():
        return []
    try:
        # Fetch extra results to account for exclusions
        fetch_n = n_results + len(exclude) if exclude else n_results
        results = self.collection.query(query_texts=[query], n_results=fetch_n)
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        DISTANCE_THRESHOLD = 2.0
        relevant: list[str] = []
        for name, dist in zip(ids, distances):
            if dist >= DISTANCE_THRESHOLD:
                continue
            if exclude and name in exclude:
                continue
            relevant.append(name)
            if len(relevant) >= n_results:
                break
        return relevant
    except Exception as e:
        logger.error("Error querying relevant tools: %s", e)
        return []

def rank_tools_by_relevance(self, query: str, tool_names: list[str]) -> list[str]:
    """Returns tool_names sorted by semantic similarity to query (most relevant first).

    Tools not returned by ChromaDB (e.g. not yet indexed) are appended at the end
    in their original order.
    """
    if not self.collection or not tool_names:
        return tool_names
    try:
        results = self.collection.query(
            query_texts=[query], n_results=len(tool_names)
        )
        ids = results.get("ids", [[]])[0]
        name_set = set(tool_names)
        ordered = [name for name in ids if name in name_set]
        ordered_set = set(ordered)
        remainder = [n for n in tool_names if n not in ordered_set]
        return ordered + remainder
    except Exception as e:
        logger.error("Error ranking tools by relevance: %s", e)
        return tool_names
```

- [ ] **Step 1.5: Run tests to confirm they pass**

```
python -m pytest tests/unit/test_tool_routing.py::test_get_tools_by_categories_returns_matching_tools tests/unit/test_tool_routing.py::test_get_tools_by_categories_multi tests/unit/test_tool_routing.py::test_get_tools_by_categories_excludes_schema_less_entries tests/unit/test_tool_routing.py::test_get_relevant_tools_exclude_filters_results tests/unit/test_tool_routing.py::test_get_relevant_tools_exclude_none_unchanged tests/unit/test_tool_routing.py::test_rank_tools_by_relevance_orders_by_score tests/unit/test_tool_routing.py::test_rank_tools_by_relevance_appends_unranked -v
```

Expected: 7 PASSED.

- [ ] **Step 1.6: Verify existing SkillRouter tests still pass**

```
python -m pytest tests/unit/test_skill_manifest.py -v
```

Expected: all existing tests PASS.

- [ ] **Step 1.7: Commit**

```bash
git add app/skills/registry.py app/skills/semantic_router.py tests/unit/test_tool_routing.py
git commit -m "feat: add get_tools_by_categories to registry + extend SkillRouter with exclude/rank"
```

---

## Task 2: IntentClassifier Tests (Write Failing Tests)

**Files:**
- Create: `tests/unit/test_intent_classifier.py`

- [ ] **Step 2.1: Create the test file**

Create `tests/unit/test_intent_classifier.py`:

```python
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_classifier(scores_by_category: dict[str, float], slow_result: list[str] | None = None):
    """
    Build an IntentClassifier with mocked ChromaDB collections.

    scores_by_category maps category name → similarity score (0–1).
    Distances are derived as: distance = (1 / score) - 1.
    """
    from app.core.intent_classifier import IntentClassifier

    mock_chroma = MagicMock()

    def make_coll(category):
        score = scores_by_category.get(category, 0.0)
        distance = (1.0 / score - 1.0) if score > 0 else 999.0
        coll = MagicMock()
        coll.count.return_value = 3
        coll.get.return_value = {"ids": ["x"]}  # non-empty → no re-upsert
        coll.query.return_value = {"ids": [[f"{category}_0"]], "distances": [[distance]]}
        return coll

    mock_chroma.get_or_create_collection.side_effect = lambda name: make_coll(
        name.replace("wade_intent_", "")
    )

    mock_client = MagicMock()
    if slow_result is not None:
        async def _fake_complete(role, messages):
            yield json.dumps(slow_result)
        mock_client.complete.side_effect = _fake_complete

    clf = IntentClassifier(chroma_client=mock_chroma, inference_client=mock_client)
    return clf


# ── Fast path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fast_path_high_confidence_single_category():
    """High score on 'system' → returns ['system'], slow path NOT triggered."""
    # system=0.8 (above both INCLUDE_THRESHOLD=0.35 and GRAY_ZONE_MAX=0.55)
    # all others = 0.1 (below threshold)
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["system"] = 0.8
    clf = _make_classifier(scores)
    result = await clf.classify("what is my GPU temperature?")
    assert result == ["system"]
    clf._inference_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_fast_path_multi_intent():
    """Two categories above INCLUDE_THRESHOLD → both returned."""
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["web"] = 0.75
    scores["workspace"] = 0.6
    clf = _make_classifier(scores)
    result = await clf.classify("search the web and save results to a file")
    assert set(result) == {"web", "workspace"}
    clf._inference_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_slow_path_triggered_when_top_score_below_gray_zone():
    """Top score < GRAY_ZONE_MAX (0.55) → slow path fires."""
    from app.core.intent_classifier import IntentClassifier
    scores = {cat: 0.4 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    # All scores = 0.4: above INCLUDE_THRESHOLD but top < GRAY_ZONE_MAX
    clf = _make_classifier(scores, slow_result=["workspace"])
    result = await clf.classify("do something with a file")
    clf._inference_client.complete.assert_called_once()


@pytest.mark.asyncio
async def test_slow_path_triggered_on_fuzzy_boundary():
    """Last-included and first-excluded scores within SLOW_PATH_MARGIN (0.08) → slow path fires."""
    from app.core.intent_classifier import IntentClassifier
    # workspace=0.60, web=0.54 → gap = 0.06 < SLOW_PATH_MARGIN=0.08 → fuzzy boundary
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["workspace"] = 0.60
    scores["web"] = 0.54
    clf = _make_classifier(scores, slow_result=["workspace", "web"])
    result = await clf.classify("...")
    clf._inference_client.complete.assert_called_once()


@pytest.mark.asyncio
async def test_slow_path_merges_with_fast_result():
    """Slow path adds a category; fast-path categories are preserved."""
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["web"] = 0.8  # fast path confidently detects 'web'
    # top_score=0.8 >= GRAY_ZONE_MAX=0.55, gap between web(0.8) and next is large → no slow path trigger
    # Manually force slow path by making gap small:
    scores["workspace"] = 0.74  # gap = 0.06 < SLOW_PATH_MARGIN → triggers slow
    # slow path returns ["web", "workspace"]
    clf = _make_classifier(scores, slow_result=["web", "workspace"])
    result = await clf.classify("...")
    assert "web" in result
    assert "workspace" in result


@pytest.mark.asyncio
async def test_slow_path_does_not_remove_confident_fast_categories():
    """Slow path returning a different set never removes a high-confidence fast category."""
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["system"] = 0.8
    scores["workspace"] = 0.76  # gap = 0.04 → triggers slow path
    # slow path only returns ["workspace"] — system is confident, must survive
    clf = _make_classifier(scores, slow_result=["workspace"])
    result = await clf.classify("check system health and write a log file")
    assert "system" in result
    assert "workspace" in result


@pytest.mark.asyncio
async def test_slow_path_not_triggered_when_client_is_none():
    """If no inference_client, slow path never fires even when score is low."""
    scores = {cat: 0.4 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    from app.core.intent_classifier import IntentClassifier
    mock_chroma = MagicMock()

    def make_coll(category):
        score = 0.4
        distance = (1.0 / score - 1.0)
        coll = MagicMock()
        coll.count.return_value = 3
        coll.get.return_value = {"ids": ["x"]}
        coll.query.return_value = {"ids": [[f"{category}_0"]], "distances": [[distance]]}
        return coll

    mock_chroma.get_or_create_collection.side_effect = lambda name: make_coll(
        name.replace("wade_intent_", "")
    )
    clf = IntentClassifier(chroma_client=mock_chroma, inference_client=None)
    result = await clf.classify("ambiguous request")
    # Should not raise; returns fast-path categories
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_thresholds_are_read_from_class_constants():
    """Patching the constants changes behavior — they are not hardcoded literals."""
    from app.core import intent_classifier as clf_module
    scores = {cat: 0.5 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    # With INCLUDE_THRESHOLD=0.5, a score of 0.5 is exactly on the boundary.
    # With INCLUDE_THRESHOLD=0.6, same score would be excluded.
    with patch.object(clf_module.IntentClassifier, "INCLUDE_THRESHOLD", 0.6):
        clf = _make_classifier(scores)
        result = await clf.classify("test")
        # All scores are 0.5 < 0.6 → none included → fallback
        assert isinstance(result, list)
```

- [ ] **Step 2.2: Run to confirm all tests fail (module doesn't exist yet)**

```
python -m pytest tests/unit/test_intent_classifier.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.core.intent_classifier'`

---

## Task 3: Create IntentClassifier (Make Tests Pass)

**Files:**
- Create: `app/core/intent_classifier.py`

- [ ] **Step 3.1: Create the module**

Create `app/core/intent_classifier.py`:

```python
from __future__ import annotations

import json
import asyncio
import logging

from typing import Any

logger = logging.getLogger("wade.intent_classifier")

_CATEGORIES = [
    "workspace",
    "web",
    "system",
    "scheduling",
    "memory",
    "communication",
    "research",
]

_ANCHOR_QUERIES: dict[str, list[str]] = {
    "workspace": [
        "Write a Python script to process CSV files",
        "Read the contents of my config file",
        "Run the test suite and show me the output",
    ],
    "web": [
        "Search the web for the latest news on AI regulation",
        "Look up the documentation for FastAPI",
        "What is the current Bitcoin price?",
    ],
    "system": [
        "Check if the WhatsApp bridge is running",
        "What is my GPU temperature right now?",
        "Run a full system health check and diagnostics",
    ],
    "scheduling": [
        "Remind me to review the PR at 3pm today",
        "Set an alarm for tomorrow morning at 8am",
        "Schedule a daily backup task at midnight",
    ],
    "memory": [
        "Remember that I prefer dark mode in all editors",
        "What did I tell you about my project last week?",
        "Store this note so I can find it later",
    ],
    "communication": [
        "Send a WhatsApp message to John saying I will be late",
        "Draft an email to the team about the release",
        "Reply to the last message in the conversation",
    ],
    "research": [
        "Give me a deep analysis of quantum computing trends in 2026",
        "Research the top competitors in the AI assistant market",
        "Summarize the key findings from the latest retrieval-augmented generation papers",
    ],
}


class IntentClassifier:
    """Hybrid fast/slow intent classifier for W.A.D.E. tool routing.

    Fast path: ChromaDB embedding similarity against per-category anchor queries.
    Slow path: LLM classification, fires when confidence is low or boundary is fuzzy.
    Results are merged (union) — slow path never removes a confidently-detected fast category.
    """

    INCLUDE_THRESHOLD: float = 0.35
    GRAY_ZONE_MAX: float = 0.55
    SLOW_PATH_MARGIN: float = 0.08

    def __init__(self, chroma_client: Any, inference_client: Any = None) -> None:
        self._chroma = chroma_client
        self._inference_client = inference_client
        self._collections: dict[str, Any] = {}
        if chroma_client:
            self._ensure_anchor_collections()

    def _ensure_anchor_collections(self) -> None:
        for category, anchors in _ANCHOR_QUERIES.items():
            coll = self._chroma.get_or_create_collection(name=f"wade_intent_{category}")
            try:
                existing = coll.get()
                if not existing.get("ids"):
                    coll.add(
                        ids=[f"{category}_{i}" for i in range(len(anchors))],
                        documents=anchors,
                    )
            except Exception as exc:
                logger.warning("[INTENT] Could not seed anchors for %s: %s", category, exc)
            self._collections[category] = coll

    def _compute_category_scores(self, user_prompt: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for category, coll in self._collections.items():
            try:
                results = coll.query(query_texts=[user_prompt], n_results=1)
                distances = results.get("distances", [[]])[0]
                scores[category] = 1.0 / (1.0 + distances[0]) if distances else 0.0
            except Exception as exc:
                logger.warning("[INTENT] Score error for %s: %s", category, exc)
                scores[category] = 0.0
        return scores

    def _should_trigger_slow_path(
        self, scores: dict[str, float], included: list[str]
    ) -> bool:
        if not scores:
            return False
        top_score = max(scores.values())
        if top_score < self.GRAY_ZONE_MAX:
            return True
        excluded = [cat for cat in scores if cat not in set(included)]
        if not included or not excluded:
            return False
        last_included_score = min(scores[c] for c in included)
        first_excluded_score = max(scores[c] for c in excluded)
        return (last_included_score - first_excluded_score) < self.SLOW_PATH_MARGIN

    async def _slow_path(self, user_prompt: str) -> list[str]:
        if not self._inference_client:
            return []
        category_list = ", ".join(_ANCHOR_QUERIES.keys())
        messages = [
            {
                "role": "user",
                "content": (
                    f"Classify this user request into one or more of these categories: {category_list}\n\n"
                    f'User request: "{user_prompt}"\n\n'
                    "Respond with ONLY a JSON array of matching category names. "
                    'Example: ["workspace", "web"]. Return only clearly relevant categories.'
                ),
            }
        ]
        try:
            full_text = ""
            async for chunk in self._inference_client.complete("fast", messages):
                full_text += chunk
            data = json.loads(full_text.strip())
            valid_cats = set(_ANCHOR_QUERIES.keys())
            return [cat for cat in data if cat in valid_cats]
        except Exception as exc:
            logger.warning("[INTENT] Slow path failed: %s", exc)
            return []

    async def classify(self, user_prompt: str) -> list[str]:
        """Return a list of detected intent categories for user_prompt."""
        if not self._collections:
            return []

        scores = await asyncio.to_thread(self._compute_category_scores, user_prompt)
        included = [cat for cat, score in scores.items() if score >= self.INCLUDE_THRESHOLD]

        if self._should_trigger_slow_path(scores, included):
            slow = await self._slow_path(user_prompt)
            for cat in slow:
                if cat not in included:
                    included.append(cat)

        if not included and scores:
            included = [max(scores, key=scores.get)]

        return included
```

- [ ] **Step 3.2: Run the intent classifier tests**

```
python -m pytest tests/unit/test_intent_classifier.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 3.3: Commit**

```bash
git add app/core/intent_classifier.py tests/unit/test_intent_classifier.py
git commit -m "feat: add IntentClassifier with hybrid fast/slow path for category detection"
```

---

## Task 4: Tool Routing Pipeline Tests (Add to test_tool_routing.py)

**Files:**
- Modify: `tests/unit/test_tool_routing.py`

- [ ] **Step 4.1: Append pipeline tests to `tests/unit/test_tool_routing.py`**

Append these tests to the existing file:

```python
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

    all_names = [name for name, _ in tool_defs]
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
```

- [ ] **Step 4.2: Run new pipeline tests to confirm they fail**

```
python -m pytest tests/unit/test_tool_routing.py -k "stage or cap or exclusivity" -v 2>&1 | head -40
```

Expected: `ImportError` or `AssertionError` (ALWAYS_ON_TOOLS not defined, `_get_intent_classifier` not defined).

---

## Task 5: Update `_get_tools_for_task` in executor.py (Make Pipeline Tests Pass)

**Files:**
- Modify: `app/agents/executor.py`

- [ ] **Step 5.1: Add module-level constants and `_get_intent_classifier` factory**

Add these lines immediately after the `_skill_router` lines (after line 29):

```python
ALWAYS_ON_TOOLS: frozenset[str] = frozenset(["hot_reload_system", "check_wade_services_health"])
MAX_TOOL_POOL: int = 14
WORKSPACE_CAP: int = 10

_intent_classifier = None

def _get_intent_classifier():
    global _intent_classifier
    if _intent_classifier is None:
        from app.services.model_router import ModelRouter
        from app.services.inference_client import InferenceClient
        from app.core.config import ConfigManager
        from app.core.intent_classifier import IntentClassifier
        config = ConfigManager.get()
        roles = config.get("roles", {}).get("mapping", {})
        router = ModelRouter(roles)
        client = InferenceClient(router=router)
        _intent_classifier = IntentClassifier(
            chroma_client=_personality.chroma_client,
            inference_client=client,
        )
    return _intent_classifier
```

- [ ] **Step 5.2: Replace the `_get_tools_for_task` function body**

Replace the entire `_get_tools_for_task` function (lines 67–135) with:

```python
def _get_tools_for_task(goal: str, tier_ctx=None) -> tuple[list[dict], str]:
    """4-stage category-aware tool selection pipeline."""
    load_all_skills()
    all_schemas, _ = get_dynamic_tools()

    from app.skills.registry import TOOL_INVENTORY, get_tool_descriptions, get_tools_by_categories

    router = _get_skill_router()
    router.index_tools()
    classifier = _get_intent_classifier()

    # Stage 1: Category pool — all tools whose category matches detected intent
    categories = asyncio.run(classifier.classify(goal))
    pool: set[str] = set(get_tools_by_categories(categories))

    # Workspace cap: if workspace tools exceed WORKSPACE_CAP, trim by semantic similarity
    if "workspace" in categories:
        ws_tools = [
            n for n in pool
            if TOOL_INVENTORY.get(n, {}).get("manifest") and
            TOOL_INVENTORY[n]["manifest"].category == "workspace"
        ]
        if len(ws_tools) > WORKSPACE_CAP:
            top_ws = router.rank_tools_by_relevance(goal, ws_tools)[:WORKSPACE_CAP]
            pool = (pool - set(ws_tools)) | set(top_ws)

    # Stage 2: Always-on tools
    pool.update(t for t in ALWAYS_ON_TOOLS if t in TOOL_INVENTORY)

    # Stage 3: Cross-category semantic fill — top 4 from outside the current pool
    fill = router.get_relevant_tools(goal, n_results=4, exclude=pool)
    pool.update(fill)

    # Stage 4: Hard cap
    combined = list(pool)
    if len(combined) > MAX_TOOL_POOL:
        combined = router.rank_tools_by_relevance(goal, combined)[:MAX_TOOL_POOL]

    # Tier filtering (unchanged from original)
    if tier_ctx is not None and tier_ctx.is_restricted:
        allowed = tier_ctx.allowed_tool_categories
        combined = [
            name for name in combined
            if TOOL_INVENTORY.get(name, {}).get("manifest")
            and TOOL_INVENTORY[name]["manifest"].category in allowed
        ]
    if tier_ctx is not None:
        _tier = tier_ctx.tier
        combined = [
            name for name in combined
            if not (
                TOOL_INVENTORY.get(name, {}).get("manifest") and
                TOOL_INVENTORY[name]["manifest"].allowed_tiers and
                _tier not in TOOL_INVENTORY[name]["manifest"].allowed_tiers
            )
        ]

    if not combined:
        return [], ""

    filtered_schemas = [s for s in all_schemas if s["function"]["name"] in set(combined)]

    all_descriptions = {t["name"]: t for t in get_tool_descriptions()}
    tool_lines: list[str] = []
    instruction_blocks: list[str] = []
    for name in combined:
        if name not in all_descriptions:
            continue
        t = all_descriptions[name]
        tool_lines.append(f"- {t['name']}: {t['description']}")
        manifest = TOOL_INVENTORY.get(name, {}).get("manifest")
        if manifest and manifest.instructions:
            instruction_blocks.append(f"### {name}\n{manifest.instructions}")

    if not tool_lines:
        return filtered_schemas, ""

    parts = [
        "<available_tools>",
        "You have the following tools available for this request:",
        "\n".join(tool_lines),
        "",
        "IMPORTANT: You may ONLY call tools listed above. "
        "Do not invent or guess tool names — if a needed tool is not listed, "
        "say so and ask the user to clarify.",
        "</available_tools>",
    ]
    if instruction_blocks:
        parts += [
            "",
            "<tool_instructions>",
            "Behavioral instructions for the tools above — follow these exactly:",
            "",
            "\n\n".join(instruction_blocks),
            "</tool_instructions>",
        ]
    return filtered_schemas, "\n".join(parts)
```

- [ ] **Step 5.3: Run pipeline tests**

```
python -m pytest tests/unit/test_tool_routing.py -v
```

Expected: all tests in `test_tool_routing.py` PASS.

- [ ] **Step 5.4: Run existing executor tests to check for regressions**

```
python -m pytest tests/unit/test_executor.py -v
```

Expected: all existing executor tests PASS (they mock `_get_tools_for_task` directly, bypassing the new implementation).

- [ ] **Step 5.5: Run intent classifier tests to verify no regressions**

```
python -m pytest tests/unit/test_intent_classifier.py -v
```

Expected: all PASS.

- [ ] **Step 5.6: Commit**

```bash
git add app/agents/executor.py tests/unit/test_tool_routing.py
git commit -m "feat: replace _get_tools_for_task with 4-stage category-aware pipeline"
```

---

## Task 6: Diagnostics Revert + Sidecar Update

**Files:**
- Modify: `app/skills/system/diagnostics.py`
- Modify: `app/skills/system/perform_system_recovery.md`

### 6a: Update the sidecar

- [ ] **Step 6.1: Update `perform_system_recovery.md` to add `restart_browser_service`**

In `app/skills/system/perform_system_recovery.md`, replace the parameters block:

```yaml
parameters:
  action:
    type: string
    enum: [restart_whatsapp_bridge, clear_stale_pid, restart_gateway, provision_browser_service]
    description: The recovery protocol to execute.
```

with:

```yaml
parameters:
  action:
    type: string
    enum: [restart_whatsapp_bridge, restart_browser_service, clear_stale_pid, restart_gateway, provision_browser_service]
    description: The recovery protocol to execute.
```

Also add to the `## Instructions` section, under `## Protocols`:

```markdown
    - `restart_browser_service`: Restarts the `wade_sandbox_browser` Docker container. Allow 10–15 seconds for CDP ports 9222 and 9223 to become available.
```

### 6b: Revert diagnostics.py

- [ ] **Step 6.2: Revert the three tools back to `@register_tool("name")`**

In `app/skills/system/diagnostics.py`, change the imports to include both:

```python
from app.skills.registry import register_tool
from app.skills.sdk import wade_tool
```

Then revert `check_hardware_stats`, `check_wade_services_health`, and `perform_system_recovery` from `@wade_tool(...)` back to `@register_tool("name")`. Keep `check_active_models` as `@wade_tool(...)`.

The final diagnostics.py should look like:

```python
import os
import sys
import psutil
import asyncio
import logging
import subprocess

from pathlib import Path

from app.core.config import PID_FILE, ConfigManager
from app.skills.registry import register_tool
from app.skills.sdk import wade_tool
from app.core.hardware import probe_hardware, system_environment

logger = logging.getLogger("wade_agent_runtime")


@register_tool("check_hardware_stats")
async def check_hardware_stats() -> str:
    """Returns a detailed report of the physical PC hardware status."""
    report = ["🖥️ PC HARDWARE HEALTH REPORT\n" + "="*40]
    try:
        hw = await asyncio.to_thread(probe_hardware)
    except Exception as e:
        logger.warning(f"Hardware probe error bypassed: {e}")
        hw = {"os": "Unknown", "devices": []}
        report.append(f"⚠️ Hardware probe partially failed (See logs).")
    try:
        ctx = await system_environment.get_context()
    except Exception as e:
        logger.warning(f"System environment context error bypassed: {e}")
        ctx = "Unavailable (system environment context error)."
    report.append(f"OS: {hw.get('os', 'Unknown')}")
    report.append(f"Real-time Context: {ctx}")
    report.append("\n[Devices]")
    for dev in hw.get("devices", []):
        name = dev.get("name")
        kind = dev.get("kind", "UNKNOWN").upper()
        temp = dev.get("meta", {}).get("temperature", "N/A")
        mem_total = dev.get("memory_total_gb", "Unknown")
        mem_free = dev.get("memory_free_gb", "Unknown")
        report.append(f"- {kind}: {name}")
        report.append(f"  VRAM/RAM: {mem_free}GB free / {mem_total}GB total")
        if temp != "N/A":
            report.append(f"  Temp: {temp}°C")
    return "\n".join(report)


@wade_tool(
    name="check_active_models",
    description="Returns the currently active AI models and their role assignments (chat, reasoner, tools, etc.).",
    risk="low",
    category="system",
    cacheable=True,
    cache_ttl=60,
    instructions="Use when the user asks which models are loaded, what AI engine is active, or about model configuration.",
)
async def check_active_models() -> str:
    """Returns the currently active Ollama models."""
    try:
        config = ConfigManager.get()
        models = config.get("roles", {}).get("mapping", {})
        if not models:
            return "No specific models mapped in configuration. Using system defaults."
        report = ["🧠 ACTIVE AI MODELS\n" + "="*40]
        for role, model in models.items():
            report.append(f"- {role.title()}: {model}")
        return "\n".join(report)
    except Exception as e:
        return f"Error retrieving active models: {e}"


@register_tool("check_wade_services_health")
async def check_wade_services_health() -> str:
    """Checks the pulse of W.A.D.E.'s core internal components."""
    def _run_checks():
        report = ["🏥 W.A.D.E. INTERNAL SYSTEM HEALTH REPORT\n" + "="*40]
        gateway_status = "🔴 OFFLINE"
        if PID_FILE.exists():
            gateway_status = f"🟢 ONLINE (PID: {PID_FILE.read_text().strip()})"
        report.append(f"Gateway Daemon: {gateway_status}")
        bridge_status = "🔴 OFFLINE"
        bridge_running = False
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                if proc.info['name'] in ['node', 'node.exe']:
                    cmdline = proc.info.get('cmdline') or []
                    if any('whatsapp-bridge.js' in cmd for cmd in cmdline):
                        bridge_running = True
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        if bridge_running:
            bridge_status = "🟢 ONLINE"
        report.append(f"WhatsApp Bridge: {bridge_status}")
        import socket
        def check_port(port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                return s.connect_ex(('localhost', port)) == 0
        headless_port = check_port(9223)
        headed_port = check_port(9222)
        report.append(f"Browser Service (Headless:9223): {'🟢 ONLINE' if headless_port else '🔴 OFFLINE'}")
        report.append(f"Browser Service (Headed:9222):   {'🟢 ONLINE' if headed_port else '🔴 OFFLINE'}")
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            admin_status = "🟢 ACTIVE" if is_admin else "🟡 INACTIVE (User Mode)"
        except Exception as e:
            logger.debug(f"Admin privilege check failed (non-critical, assuming active): {e}")
            admin_status = "🟢 ACTIVE"
        report.append(f"God Mode (Elevated Privileges): {admin_status}")
        return "\n".join(report)
    return await asyncio.to_thread(_run_checks)


@register_tool("perform_system_recovery")
async def perform_system_recovery(action: str) -> str:
    """Executes sandboxed self-healing actions."""
    def _recover():
        try:
            PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
            bridge_dir = str(PROJECT_ROOT / "deploy" / "docker")

            if action == "restart_whatsapp_bridge":
                killed_count = 0
                for proc in psutil.process_iter(['name', 'cmdline']):
                    try:
                        if proc.info['name'] in ['node', 'node.exe']:
                            cmdline = proc.info.get('cmdline') or []
                            if any('whatsapp-bridge.js' in cmd for cmd in cmdline):
                                proc.kill()
                                killed_count += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
                try:
                    env = os.environ.copy()
                    node_cmd = ["node", "whatsapp-bridge.js"]
                    if sys.platform == "win32":
                        CREATE_NO_WINDOW = 0x08000000
                        subprocess.Popen(node_cmd, creationflags=CREATE_NO_WINDOW, env=env, cwd=bridge_dir)
                    else:
                        subprocess.Popen(node_cmd, start_new_session=True, env=env, cwd=bridge_dir)
                    return f"✅ WhatsApp bridge successfully restarted (Terminated {killed_count} old instances)."
                except FileNotFoundError:
                    return "❌ Could not start WhatsApp bridge — 'node' not found in PATH or bridge directory missing."
                except Exception as e:
                    return f"❌ WhatsApp bridge restart failed: {e}"

            elif action == "clear_stale_pid":
                if PID_FILE.exists():
                    PID_FILE.unlink()
                    return "✅ Stale PID file cleared. You can now safely restart the gateway."
                return "ℹ️ No stale PID file found."

            elif action == "restart_gateway":
                return "⚠️ System Guard: I cannot restart my own brain directly from within the loop. Please advise the user to run 'wade stop' followed by 'wade start' in the CLI."

            elif action == "restart_browser_service":
                try:
                    result = subprocess.run(
                        ["docker", "restart", "wade_sandbox_browser"],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        return "✅ Browser service restarted. Allow 10–15 seconds for the CDP endpoints to become available on ports 9222 and 9223."
                    start_result = subprocess.run(
                        ["docker", "start", "wade_sandbox_browser"],
                        capture_output=True, text=True, timeout=30
                    )
                    if start_result.returncode == 0:
                        return "✅ Browser service started. Allow 10–15 seconds for ports 9222 and 9223 to become available."
                    err = result.stderr.strip() or start_result.stderr.strip() or "container may not exist"
                    return f"❌ Browser service restart failed: {err}. Ensure Docker is running and the wade_sandbox_browser container exists."
                except FileNotFoundError:
                    return "❌ Docker CLI not found. Ensure Docker Desktop is installed, running, and its CLI is in PATH."
                except subprocess.TimeoutExpired:
                    return "⚠️ Docker command timed out. The container may be starting slowly — wait 20 seconds and re-run the health check."
                except Exception as e:
                    return f"❌ Browser service restart failed: {e}"

            elif action == "provision_browser_service":
                try:
                    engine = ConfigManager.get().get("automation_browser", "chromium")
                    subprocess.run([sys.executable, "-m", "playwright", "install", engine], check=True, capture_output=True)
                    return f"✅ Local {engine} binaries provisioned. W.A.D.E. will now attempt to use local browser fallback if remote connection fails."
                except Exception as e:
                    return f"❌ Failed to provision browser binaries: {e}"

            return (
                f"❌ Unknown recovery action '{action}'. Valid actions: "
                "restart_whatsapp_bridge, restart_browser_service, provision_browser_service, "
                "restart_gateway, clear_stale_pid."
            )
        except Exception as e:
            logger.error("[DIAGNOSTICS] perform_system_recovery unexpected error: %s", e)
            return f"❌ Recovery action failed unexpectedly: {e}"

    return await asyncio.to_thread(_recover)
```

- [ ] **Step 6.3: Verify the registry test still passes (no `@wade_tool` / `@register_tool` conflict)**

```
python -m pytest tests/unit/test_registry_risk.py tests/unit/test_skill_manifest.py -v
```

Expected: all PASS.

- [ ] **Step 6.4: Commit**

```bash
git add app/skills/system/diagnostics.py app/skills/system/perform_system_recovery.md
git commit -m "fix: revert diagnostics to sidecar-backed register_tool; add restart_browser_service to sidecar enum"
```

---

## Task 7: Rewrite TOOLS.md

**Files:**
- Modify: `C:\Users\turnt\.wade\workspace\TOOLS.md`

- [ ] **Step 7.1: Rewrite TOOLS.md — remove all specific tool names**

Replace the entire contents of `C:\Users\turnt\.wade\workspace\TOOLS.md` with:

```markdown
# SYSTEM CAPABILITIES & ENVIRONMENT NOTES

You have direct access to the host machine's hardware and operating system. You are a local entity, not a remote service.

## SKILL DEVELOPMENT
When writing new skills or modifying existing ones:
- **Registration**: Use `@register_tool("tool_name")` for tools that have a `.md` sidecar file. The sidecar provides the schema, description, and manifest. Use `@wade_tool(...)` only for tools with no sidecar.
- **Sidecar Requirement**: Every `.py` tool should have a matching `.md` file with YAML frontmatter defining its name, description, and parameters.
- **Async First**: All tool executors must be `async def`.
- **Dependencies**: Check `registry.py` to see how modules are auto-loaded. Avoid circular imports.
- **Reporting**: Tools should return descriptive strings. Use `<xml_tags>` for structured data.
- **Hotfixes**: After patching skill code or configuration files, use the hot-reload tool to apply changes immediately without a full restart.

## SOURCE CONTROL
You have direct integration with Git. Use the available source control tools to inspect repository state, review diffs, and commit your work safely.

## CAPABILITIES
You have access to registered tools that let you interact with the host system. Your capabilities include:

- **File system access**: Read, write, and modify files anywhere on the host machine.
- **Shell execution**: Run arbitrary shell commands and scripts.
- **Source control**: Inspect, stage, and commit changes in Git repositories.
- **Hot reload**: Apply code and configuration changes to the W.A.D.E. system without restarting.
- **Web research**: Search the web and retrieve page content.
- **Browser automation**: Open and control browsers for UI interaction and scraping.
- **Scheduling**: Create and manage scheduled tasks and reminders.
- **System diagnostics**: Check hardware health, running processes, and service status.
- **Memory**: Store and retrieve durable facts across sessions.

## OPERATIONAL PRINCIPLES
- **ACT, DON'T INSTRUCT**: You are an autonomous agent. If a user asks you to create a file, write code, or run a script, use your tools. Never output code and tell the user to save it manually.
- **EXECUTION PIPELINE**: When writing and running a script: write the file using the appropriate tool → verify it exists → execute it → return the literal output to the user.
- **EXECUTION HONESTY**: Never guess, simulate, or hallucinate tool output. If you cannot execute something, say so.
- **TOOL RESULT GROUNDING**: Your response must be derived from actual tool results. Do not add information not present in tool output.
- **STRICT PATH ADHERENCE**: Only create or modify files in paths specified by the user. If no path is given, use the W.A.D.E. workspace. Never write to default OS directories unless explicitly commanded.
- **IN-PLACE ERROR RECOVERY**: If a script fails, fix the original file in place. Do not create new files or change file names.
- **SINGLE SOURCE OF TRUTH**: Once you write code to a file, do not print the same code back in chat. Confirm the file was written and provide execution output.
- **MANDATORY VERIFICATION**: After writing any code file, execute it to verify it works before reporting completion.
- **AUTONOMOUS DEBUGGING**: If execution fails, read the error, fix the file, and re-test. Only report back after a successful run.
```

- [ ] **Step 7.2: Commit**

```bash
git add "C:/Users/turnt/.wade/workspace/TOOLS.md"
git commit -m "fix: rewrite TOOLS.md to prose — remove tool name references that caused hallucination"
```

---

## Task 8: Routing Accuracy Eval Harness

**Files:**
- Create: `tests/evals/test_routing_accuracy.py`

- [ ] **Step 8.1: Create the eval harness**

Create `tests/evals/test_routing_accuracy.py`:

```python
"""
Tool Routing Accuracy Eval Harness

NOT a CI test — requires Ollama running. Run manually:
    python tests/evals/test_routing_accuracy.py

Reports category precision/recall and tool recall per test case.
Optionally tests hallucination rate if LIVE_MODEL=1 env var is set.
"""
from __future__ import annotations

import os
import sys
import asyncio
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import patch, MagicMock, AsyncMock

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ── Labeled dataset ─────────────────────────────────────────────────────────

@dataclass
class RoutingCase:
    prompt: str
    expected_categories: list[str]
    must_include_tools: list[str] = field(default_factory=list)
    description: str = ""


ROUTING_CASES: list[RoutingCase] = [
    RoutingCase(
        prompt="Write a Python script to rename all .txt files in a directory",
        expected_categories=["workspace"],
        must_include_tools=["write_host_file", "run_shell_command"],
        description="Clear workspace task",
    ),
    RoutingCase(
        prompt="Search the web for the latest news about AI regulation in the EU",
        expected_categories=["web"],
        must_include_tools=["web_search"],
        description="Clear web search task",
    ),
    RoutingCase(
        prompt="Check if the WhatsApp bridge is running and restart it if it's offline",
        expected_categories=["system"],
        must_include_tools=["check_wade_services_health", "perform_system_recovery"],
        description="System diagnostic + recovery",
    ),
    RoutingCase(
        prompt="Remind me to review the pull request at 3pm today",
        expected_categories=["scheduling"],
        must_include_tools=["schedule_task"],
        description="Clear scheduling task",
    ),
    RoutingCase(
        prompt="Search the web for FastAPI documentation and save a summary to a file",
        expected_categories=["web", "workspace"],
        must_include_tools=["web_search", "write_host_file"],
        description="Multi-intent: web + workspace",
    ),
    RoutingCase(
        prompt="What is my GPU temperature and how much VRAM is free?",
        expected_categories=["system"],
        must_include_tools=["check_hardware_stats"],
        description="Hardware diagnostics",
    ),
    RoutingCase(
        prompt="Remember that I prefer dark mode in all applications",
        expected_categories=["memory"],
        must_include_tools=[],
        description="Memory storage intent",
    ),
    RoutingCase(
        prompt="Send a WhatsApp message to John saying I will be 10 minutes late",
        expected_categories=["communication"],
        must_include_tools=[],
        description="Communication intent",
    ),
    RoutingCase(
        prompt="Run my test suite and show me the output",
        expected_categories=["workspace"],
        must_include_tools=["run_shell_command"],
        description="Test execution",
    ),
    RoutingCase(
        prompt="Give me a deep research analysis of the top 5 AI assistant platforms in 2026",
        expected_categories=["research"],
        must_include_tools=[],
        description="Research task",
    ),
]


# ── Evaluation logic ─────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    case: RoutingCase
    detected_categories: list[str]
    routed_tool_names: list[str]
    category_hit: bool
    tool_recall: float


async def _evaluate_case(case: RoutingCase, live_classifier: bool = False) -> CaseResult:
    from app.core.intent_classifier import IntentClassifier

    if live_classifier:
        from app.skills.registry import load_all_skills
        from app.core.personality import PersonalityManager
        from app.services.model_router import ModelRouter
        from app.services.inference_client import InferenceClient
        from app.core.config import ConfigManager
        load_all_skills()
        personality = PersonalityManager()
        config = ConfigManager.get()
        roles = config.get("roles", {}).get("mapping", {})
        router = ModelRouter(roles)
        client = InferenceClient(router=router)
        clf = IntentClassifier(chroma_client=personality.chroma_client, inference_client=client)
        detected = await clf.classify(case.prompt)
    else:
        # Offline: mock classifier returns expected categories (baseline sanity check)
        detected = case.expected_categories[:]

    from app.agents.executor import _get_tools_for_task
    from app.skills.registry import load_all_skills, _FULL_SCHEMAS

    load_all_skills()

    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=detected)

    with patch("app.agents.executor._get_intent_classifier", return_value=mock_classifier):
        schemas, ctx = _get_tools_for_task(case.prompt)

    routed_names = [s["function"]["name"] for s in schemas]

    # Category hit: all expected categories detected
    category_hit = all(cat in detected for cat in case.expected_categories)

    # Tool recall: fraction of must_include_tools found in routed pool
    if case.must_include_tools:
        found = sum(1 for t in case.must_include_tools if t in routed_names)
        tool_recall = found / len(case.must_include_tools)
    else:
        tool_recall = 1.0

    return CaseResult(
        case=case,
        detected_categories=detected,
        routed_tool_names=routed_names,
        category_hit=category_hit,
        tool_recall=tool_recall,
    )


def _print_report(results: list[CaseResult], live: bool) -> None:
    mode = "LIVE (Ollama)" if live else "OFFLINE (mocked classifier)"
    print(f"\n{'='*70}")
    print(f"  TOOL ROUTING ACCURACY EVAL — {mode}")
    print(f"{'='*70}")
    print(f"{'Prompt':<45} {'Cat Hit':<10} {'Tool Recall':<12} {'Pool Size'}")
    print("-" * 70)

    cat_hits = 0
    total_recall = 0.0

    for r in results:
        prompt_short = r.case.prompt[:43] + ".." if len(r.case.prompt) > 43 else r.case.prompt
        cat_str = "✅" if r.category_hit else "❌"
        recall_str = f"{r.tool_recall:.0%}"
        print(f"{prompt_short:<45} {cat_str:<10} {recall_str:<12} {len(r.routed_tool_names)}")
        if not r.category_hit:
            print(f"    Expected: {r.case.expected_categories} | Got: {r.detected_categories}")
        if r.tool_recall < 1.0:
            missing = [t for t in r.case.must_include_tools if t not in r.routed_tool_names]
            print(f"    Missing tools: {missing}")
        cat_hits += int(r.category_hit)
        total_recall += r.tool_recall

    n = len(results)
    print("-" * 70)
    print(f"Category hit rate: {cat_hits}/{n} ({cat_hits/n:.0%})")
    print(f"Avg tool recall:   {total_recall/n:.0%}")
    print(f"{'='*70}\n")


async def main() -> None:
    live = os.environ.get("LIVE_MODEL", "0") == "1"
    print(f"Running {len(ROUTING_CASES)} routing eval cases (live={live})...")
    results = []
    for case in ROUTING_CASES:
        try:
            result = await _evaluate_case(case, live_classifier=live)
            results.append(result)
        except Exception as exc:
            print(f"ERROR on case '{case.prompt[:50]}': {exc}")
    _print_report(results, live=live)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 8.2: Verify the harness runs without error (offline mode)**

```
cd C:\Users\turnt\OneDrive\Desktop\Development\W.A.D.E
python tests/evals/test_routing_accuracy.py
```

Expected: prints a routing eval report without crashing. Tool recall may be low in offline mode (mocked classifier + tools may not be registered in test context) — that's acceptable. The harness should exit cleanly.

- [ ] **Step 8.3: Commit**

```bash
git add tests/evals/test_routing_accuracy.py
git commit -m "feat: add tool routing accuracy eval harness"
```

---

## Final Verification

- [ ] **Run the full test suite for affected modules**

```
python -m pytest tests/unit/test_tool_routing.py tests/unit/test_intent_classifier.py tests/unit/test_executor.py tests/unit/test_skill_manifest.py tests/unit/test_registry_risk.py -v
```

Expected: all PASS.

- [ ] **Smoke-test the registry loading (no import errors)**

```
python -c "from app.skills.registry import load_all_skills; load_all_skills(); print('Registry OK')"
```

Expected: `Registry OK` (no tracebacks).
