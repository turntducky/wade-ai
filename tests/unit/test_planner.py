import json
import pytest

from unittest.mock import AsyncMock, MagicMock

from app.agents.critic import GoalAnchor
from app.services.model_router import ModelRouter
from app.services.inference_client import InferenceClient
from app.agents.planner import PlannerAgent, _is_simple

def make_planner() -> PlannerAgent:
    router = ModelRouter({"planner": "qwen2.5:14b", "fast": "qwen2.5:3b"})
    client = InferenceClient(router=router)
    client.chat = AsyncMock()
    return PlannerAgent(client)

def test_is_simple_returns_true_for_short_question():
    assert _is_simple("what time is it?") is True

def test_is_simple_returns_true_for_short_wh_query():
    assert _is_simple("who is the president?") is True

def test_is_simple_returns_false_for_multi_step_goal():
    goal = "research the latest AI papers, analyze them, and save a report to my workspace"
    assert _is_simple(goal) is False

@pytest.mark.asyncio
async def test_decompose_returns_anchor_and_single_task_for_simple_goal():
    planner = make_planner()
    anchor, tasks = await planner.decompose("what is 2+2?")
    assert isinstance(anchor, GoalAnchor)
    assert len(tasks) == 1
    assert tasks[0].goal == "what is 2+2?"

@pytest.mark.asyncio
async def test_decompose_returns_anchor_with_success_criteria():
    planner = make_planner()
    plan_obj = {
        "success_criteria": ["Web search results for X retrieved", "Synthesis complete"],
        "constraints": ["Do not delete files"],
        "steps": [
            {"goal": "search web for X", "expected_outcome": "URLs returned",
             "depends_on": [], "requires_network": True, "is_reversible": True},
            {"goal": "search web for Y", "expected_outcome": "URLs returned",
             "depends_on": [], "requires_network": True, "is_reversible": True},
            {"goal": "synthesize results", "expected_outcome": "Summary paragraph",
             "depends_on": [0, 1], "requires_network": False, "is_reversible": True},
        ]
    }
    planner._client.chat.return_value = (json.dumps(plan_obj), [])  # type: ignore
    anchor, tasks = await planner.decompose(
        "research X and Y then write a synthesis report and save it"
    )
    assert isinstance(anchor, GoalAnchor)
    assert anchor.success_criteria == ["Web search results for X retrieved", "Synthesis complete"]
    assert anchor.constraints == ["Do not delete files"]
    assert len(tasks) == 3

@pytest.mark.asyncio
async def test_decompose_depends_on_stored_as_task_ids():
    planner = make_planner()
    plan_obj = {
        "success_criteria": ["done"],
        "constraints": [],
        "steps": [
            {"goal": "step A", "expected_outcome": "A done",
             "depends_on": [], "requires_network": False, "is_reversible": True},
            {"goal": "step B", "expected_outcome": "B done",
             "depends_on": [0], "requires_network": False, "is_reversible": True},
        ]
    }
    planner._client.chat.return_value = (json.dumps(plan_obj), []) # type: ignore
    _, tasks = await planner.decompose("perform step A then perform step B")
    assert len(tasks) == 2
    assert tasks[1].depends_on == [tasks[0].id]

@pytest.mark.asyncio
async def test_decompose_sets_expected_outcome_per_step():
    planner = make_planner()
    plan_obj = {
        "success_criteria": ["done"],
        "constraints": [],
        "steps": [
            {"goal": "search web", "expected_outcome": "List of relevant URLs",
             "depends_on": [], "requires_network": True, "is_reversible": True},
        ]
    }
    planner._client.chat.return_value = (json.dumps(plan_obj), []) # type: ignore
    _, tasks = await planner.decompose("research python tutorials online")
    assert tasks[0].expected_outcome == "List of relevant URLs"

@pytest.mark.asyncio
async def test_decompose_skips_network_steps_when_offline():
    planner = make_planner()
    plan_obj = {
        "success_criteria": ["done"],
        "constraints": [],
        "steps": [
            {"goal": "search web", "expected_outcome": "URLs",
             "depends_on": [], "requires_network": True, "is_reversible": True},
            {"goal": "calculate locally", "expected_outcome": "Number",
             "depends_on": [], "requires_network": False, "is_reversible": True},
        ]
    }
    planner._client.chat.return_value = (json.dumps(plan_obj), []) # type: ignore
    _, tasks = await planner.decompose("retrieve data from the web and calculate locally", network_available=False)
    assert len(tasks) == 1
    assert tasks[0].goal == "calculate locally"

@pytest.mark.asyncio
async def test_decompose_falls_back_to_single_task_on_bad_json():
    planner = make_planner()
    planner._client.chat.return_value = ("this is not json at all", []) # type: ignore
    anchor, tasks = await planner.decompose("research X and Y then write a synthesis")
    assert len(tasks) == 1
    assert isinstance(anchor, GoalAnchor)
    assert anchor.original_goal == "research X and Y then write a synthesis"
    assert anchor.success_criteria == []

@pytest.mark.asyncio
async def test_decompose_handles_legacy_array_format():
    """LLM returns old array format (no top-level object) — must still work."""
    planner = make_planner()
    plan_array = [
        {"goal": "step one", "expected_outcome": "done",
         "depends_on": [], "requires_network": False, "is_reversible": True},
        {"goal": "step two", "expected_outcome": "done",
         "depends_on": [0], "requires_network": False, "is_reversible": True},
    ]
    planner._client.chat.return_value = (json.dumps(plan_array), []) # type: ignore
    anchor, tasks = await planner.decompose(
        "research topic X, summarize findings, then save a final report"
    )
    assert len(tasks) == 2
    assert tasks[0].goal == "step one"
    assert tasks[1].depends_on == [tasks[0].id]