"""
Tool Routing Accuracy Eval Harness

NOT a CI test — requires Ollama running. Run manually:
    python tests/evals/test_routing_accuracy.py

Reports category precision/recall and tool recall per test case.
Set LIVE_MODEL=1 env var to use the actual IntentClassifier with Ollama.
"""
from __future__ import annotations

import os
import sys
import asyncio
import concurrent.futures
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
        # _get_tools_for_task is sync but calls asyncio.run() internally.
        # Run it in a thread to avoid "event loop already running" conflict.
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            schemas, ctx = await loop.run_in_executor(
                pool, lambda: _get_tools_for_task(case.prompt)
            )

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
        cat_str = "OK" if r.category_hit else "MISS"
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
