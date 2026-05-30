# Tool Routing Design — Category-Aware Tool Loading

**Date:** 2026-05-30
**Status:** Approved — ready for implementation
**Author:** Claude Code (brainstorming session)

---

## Problem Statement

W.A.D.E. hallucinates tool names in two patterns:
- **Near-miss variants**: `write_file` instead of `write_host_file`, `search_web` instead of `web_search`
- **Completely fictional tools**: names that don't exist in the registry at all

Root cause: The semantic router returns top-5 tools regardless of relevance, and the prompt does not strongly constrain the model to the listed set. With only 5 tools visible and no hard exclusivity signal, the model invents what it expects to exist.

---

## Section 1: Architecture Overview

### Approach: Category-Aware Tool Loading (Option B)

Replace the flat semantic top-5 retrieval with a 4-stage pipeline that narrows the tool search space by detected intent category before semantic ranking. The model never sees tool names it wasn't explicitly handed.

**Pipeline at inference time:**

```
User prompt
    │
    ▼
┌─────────────────────┐
│  IntentClassifier   │  Fast path (ChromaDB, ~5ms)
│  (hybrid fast/slow) │  Slow path (LLM, ~500ms, conditional)
└─────────────────────┘
    │  [categories: ["workspace", "system"]]
    ▼
┌─────────────────────┐
│  Category Pool      │  All tools whose category matches
└─────────────────────┘
    │
    ▼
┌─────────────────────┐
│  Always-On Tools    │  hot_reload_system, check_wade_services_health
└─────────────────────┘
    │
    ▼
┌─────────────────────┐
│  Cross-Category     │  Top 4 by semantic similarity (raw user prompt)
│  Semantic Fill      │  from tools NOT already in the pool
└─────────────────────┘
    │
    ▼
┌─────────────────────┐
│  Hard Cap: 14       │  Trim by semantic relevance score if over
└─────────────────────┘
    │
    ▼
  Executor prompt with <available_tools> exclusivity constraint
```

**Key property:** The model is told explicitly in the prompt that it may only call tools in the `<available_tools>` list. Hallucinating a name that isn't listed becomes a clear policy violation the model can self-correct on.

---

## Section 2: IntentClassifier (Hybrid Fast/Slow)

**Module:** `app/core/intent_classifier.py`

### Category Definitions

Each category is defined by synthetic example queries (not keyword lists) that are embedded into ChromaDB at startup. Synthetic queries produce better embedding similarity with natural language user inputs than keyword bags.

| Category | Example Anchor Queries |
|----------|------------------------|
| `workspace` | "Write a Python script to process CSV files", "Read the contents of my config file", "Run the test suite and show me the output" |
| `web` | "Search the web for the latest news on AI regulation", "Look up the documentation for FastAPI", "What is the current Bitcoin price?" |
| `system` | "Check if the WhatsApp bridge is running", "What's my GPU temperature?", "Run a system health check" |
| `scheduling` | "Remind me to review the PR at 3pm", "Set an alarm for tomorrow morning", "Schedule a daily backup at midnight" |
| `memory` | "Remember that I prefer dark mode", "What did I tell you about my project last week?", "Store this note for later" |
| `communication` | "Send a WhatsApp message to John", "Draft an email to the team", "Reply to the last message" |
| `research` | "Give me a deep analysis of quantum computing trends", "Research competitors in the AI assistant space", "Summarize the academic papers on retrieval-augmented generation" |

### Fast Path (ChromaDB Embedding)

1. Embed the user prompt using the same embedding model as the skill router.
2. Query each category's anchor document collection; take the minimum distance per category.
3. Normalize distances to similarity scores (0–1).
4. Include a category if its score ≥ `INCLUDE_THRESHOLD`.

**Constants (all configurable, not hardcoded):**

```python
INCLUDE_THRESHOLD = 0.35   # Minimum score to include a category
GRAY_ZONE_MAX    = 0.55    # Top score below this triggers slow path
SLOW_PATH_MARGIN = 0.08    # Score gap between last-included / first-excluded
                            # that also triggers slow path (fuzzy boundary)
```

### Slow Path (LLM Classification)

Triggered when **either** condition is met:
- `top_score < GRAY_ZONE_MAX` — not confidently any category
- `score[last_included] - score[first_excluded] < SLOW_PATH_MARGIN` — boundary is ambiguous

The slow path fires an LLM call (haiku-class model) with a structured classification prompt. It returns a JSON list of category names and **merges** with the fast-path result (union, not replacement). The slow path never removes a category the fast path confidently detected.

**Rationale for merge:** Fast path is calibrated for high recall; slow path adds precision on ambiguous cases. Replacing fast results risks losing confidently-detected categories on a noisy LLM call.

### Multi-Intent Handling

A user message like "search the web for yesterday's news and save the results to a file" should yield `["web", "workspace"]`. Both fast and slow paths naturally support multi-label output. The inclusion threshold and slow-path triggers are per-category, not global — a high-confidence web category does not suppress workspace classification.

---

## Section 3: `_get_tools_for_task` — 4-Stage Pipeline

**File:** `app/agents/executor.py`

Replaces the current `get_relevant_tools(query, n_results=5)` call.

### Stage 1: Category Pool

```python
categories = await intent_classifier.classify(user_prompt)
pool = registry.get_tools_by_categories(categories)
```

`registry.get_tools_by_categories()` returns all tools whose manifest `category` field matches any detected category. This is a simple set lookup — no embedding needed.

**Workspace exception:** If the workspace category is detected and the workspace pool exceeds 10 tools, trim to the top 10 by semantic similarity to the raw user prompt. Workspace is a broad category with many tools; uncapped it would consume the entire 14-tool budget.

### Stage 2: Always-On Tools

A fixed set of tools always injected regardless of category:

```python
ALWAYS_ON_TOOLS = ["hot_reload_system", "check_wade_services_health"]
```

These are added to the pool (deduplicating against Stage 1 results).

### Stage 3: Cross-Category Semantic Fill

After Stages 1–2, run a semantic query against the **remaining** registry tools (those not already in the pool):

```python
fill = semantic_router.get_relevant_tools(
    query=raw_user_prompt,   # raw user prompt, NOT category anchor phrases
    exclude=pool_tool_names,
    n_results=4,
)
```

This captures tools that are genuinely relevant to the user's request but belong to a category the classifier missed (e.g., a `memory` tool surfaced during a `workspace` task). The semantic query uses the raw user prompt to maximize relevance to actual intent.

### Stage 4: Hard Cap

```python
MAX_TOOLS = 14
if len(pool) > MAX_TOOLS:
    pool = semantic_router.rank_and_trim(pool, raw_user_prompt, MAX_TOOLS)
```

Final pool is ranked by semantic similarity and trimmed to 14.

### Prompt Exclusivity Constraint

The executor prompt changes from `<available_tools_summary>` to `<available_tools>` with an explicit instruction:

```
<available_tools>
[tool list here]
</available_tools>

You may ONLY call tools that appear in the <available_tools> list above.
Calling any tool not in this list is an error. If the right tool is not listed,
say so and ask the user to rephrase or clarify.
```

---

## Section 4: TOOLS.md Cleanup + Diagnostics Revert

### TOOLS.md Rewrite

**File:** `C:\Users\turnt\.wade\workspace\TOOLS.md`

The current TOOLS.md lists specific tool names like `write_host_file`, `run_shell_command`. This creates phantom expectations — the model reads these names during system prompt injection, learns to expect them, and then hallucinates them even when the routing pipeline doesn't surface them.

Rewrite to **prose-only capability descriptions** with no tool names:

```markdown
# SYSTEM CAPABILITIES & ENVIRONMENT NOTES

You have direct access to the host machine's hardware and operating system.
You are a local entity, not a remote service.

## CAPABILITIES

- **File system access**: Read, write, and modify files anywhere on the host.
- **Shell execution**: Run arbitrary commands and scripts.
- **Source control**: Interact with Git repositories.
- **Hot reload**: Apply code changes without restarting W.A.D.E.
...
```

### Diagnostics Revert

**File:** `app/skills/system/diagnostics.py`

The previous session converted 4 tools to `@wade_tool()` to fix schema visibility. However, 3 of them already have `.md` sidecars that define the schema. Running both causes dual registration — `@wade_tool()` overwrites the sidecar schema when the `.py` module loads, breaking the sidecar's `risk` and `parameters` definitions.

**Revert strategy:**
- `check_hardware_stats` → revert to `@register_tool("name")` (sidecar handles schema)
- `check_wade_services_health` → revert to `@register_tool("name")` (sidecar handles schema)
- `perform_system_recovery` → revert to `@register_tool("name")` (sidecar has `risk: high`, which is correct — it can restart processes; the `@wade_tool` version incorrectly set `risk="medium"`)
- `check_active_models` → **keep as `@wade_tool()`** (no sidecar exists for it)

The sidecar visibility bug (tools invisible to LLM) will be fixed permanently by the `_get_tools_for_task` overhaul in Section 3, which uses `registry.get_tools_by_categories()` — a registry lookup that includes sidecar-registered tools.

---

## Section 5: Testing Strategy

Three test files cover the new system at different levels.

### `tests/unit/test_intent_classifier.py`

Unit tests for the IntentClassifier in isolation. All ChromaDB and LLM dependencies mocked.

**Coverage:**
- Fast path: high-confidence single category → returns correct category, slow path NOT triggered
- Fast path: `top_score < GRAY_ZONE_MAX` → slow path IS triggered
- Fast path: fuzzy boundary (`last_included - first_excluded < SLOW_PATH_MARGIN`) → slow path IS triggered
- Slow path merge: fast detects `["web"]`, slow returns `["web", "workspace"]` → result is `["web", "workspace"]`
- Slow path does not remove: fast detects `["system"]` with high confidence, slow returns `["workspace"]` → result is `["system", "workspace"]`
- Multi-intent: "search the web and save results to a file" → `["web", "workspace"]`
- Threshold constants are read from config (not hardcoded) — patch config and verify behavior changes

### `tests/unit/test_tool_routing.py`

Integration tests for the 4-stage pipeline. Registry mocked with known tool sets.

**Coverage:**
- Stage 1: detected categories correctly map to tool pool
- Stage 1 workspace cap: mock registry returns 15 workspace tools → pipeline caps to 10 by semantic similarity, asserts exactly 10 workspace tools in final pool
- Stage 2: always-on tools present even when not in category pool
- Stage 3: cross-category fill uses raw user prompt as query (assert the semantic_router call receives the raw prompt, not anchors)
- Stage 4: pool of 18 tools → trimmed to 14
- End-to-end: a workspace+web multi-intent query → produces a pool containing tools from both categories plus always-on tools, within cap

### `tests/evals/test_routing_accuracy.py`

Accuracy evaluation against a labeled dataset. Not a pass/fail unit test — a measurement harness.

**Dataset format:**
```python
ROUTING_CASES = [
    {"prompt": "...", "expected_categories": ["workspace"], "must_include_tools": ["write_host_file"]},
    ...
]
```

**Metrics reported:**
- Category precision/recall per category
- Tool recall: for each case, what fraction of `must_include_tools` appeared in the final pool?
- Hallucination rate (measured separately): prompts run through a live model call; detect tool calls not in the pool

**Run as:** `python tests/evals/test_routing_accuracy.py` — prints a summary table. Not wired into CI by default (requires Ollama running).

---

## Implementation Order

1. `app/core/intent_classifier.py` — new module, no dependencies on other changes
2. `app/skills/system/diagnostics.py` — revert 3 tools (isolated change)
3. `C:\Users\turnt\.wade\workspace\TOOLS.md` — rewrite prose
4. `app/agents/executor.py` — wire up 4-stage pipeline + exclusivity constraint
5. `tests/unit/test_intent_classifier.py` — unit tests
6. `tests/unit/test_tool_routing.py` — pipeline tests
7. `tests/evals/test_routing_accuracy.py` — eval harness

---

## Open Questions / Non-Goals

- **Model used for slow path**: haiku-class for speed. Exact model configurable in `config.yaml`.
- **Category set extensibility**: adding a new category requires adding anchor queries + ChromaDB collection. No code changes in the pipeline stages themselves.
- **This design does not address**: tool argument hallucination (wrong parameter names/values). That is a separate problem addressed by JSON schema enforcement in the tool call layer.
