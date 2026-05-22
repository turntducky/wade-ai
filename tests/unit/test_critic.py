import json
import pytest

from unittest.mock import MagicMock, AsyncMock

from app.agents.critic import (
    CriticAgent, GoalAnchor, ToolTrace, CriticVerdict,
    ACCEPT_THRESHOLD, ESCALATE_THRESHOLD, CRITIC_K,
)

@pytest.fixture
def mock_client():
    client = MagicMock()
    client.chat = AsyncMock()
    return client

@pytest.fixture
def critic(mock_client):
    return CriticAgent(mock_client)

@pytest.fixture
def anchor():
    return GoalAnchor(
        original_goal="Search for Python tutorials and summarize them",
        success_criteria=[
            "At least 3 tutorial URLs retrieved from web search",
            "Summary paragraph written with source citations",
        ],
        constraints=["Do not modify any files"],
    )

@pytest.fixture
def low_risk_trace():
    return ToolTrace(
        tool_name="web_search",
        args_summary='{"query": "python tutorials"}',
        result_summary="Found 5 results: ...",
        risk="low",
        exit_status="success",
        duration_ms=420,
    )

@pytest.fixture
def high_risk_trace():
    return ToolTrace(
        tool_name="run_python",
        args_summary='{"code": "import os"}',
        result_summary="Executed successfully",
        risk="high",
        exit_status="success",
        duration_ms=1200,
    )

def test_goal_anchor_fields():
    a = GoalAnchor(original_goal="do X", success_criteria=["X done"], constraints=["no Y"])
    assert a.original_goal == "do X"
    assert a.success_criteria == ["X done"]
    assert a.constraints == ["no Y"]

def test_tool_trace_defaults():
    t = ToolTrace(
        tool_name="calculator", args_summary="{}", result_summary="42",
        risk="low", exit_status="success", duration_ms=5,
    )
    assert t.was_retried is False
    assert t.intent_alignment is None

def test_critic_verdict_defaults():
    v = CriticVerdict(status="ok", confidence=0.9)
    assert v.reason is None
    assert v.revised_steps is None
    assert v.surface_to_user is False
    assert v.goal_progress_delta is None

def test_thresholds():
    assert ACCEPT_THRESHOLD == 0.68
    assert ESCALATE_THRESHOLD == 0.55
    assert CRITIC_K == 5

@pytest.mark.asyncio
async def test_validate_anchor_structure_ok(critic, anchor, mock_client):
    mock_client.chat.return_value = (
        json.dumps({"status": "ok", "confidence": 0.92, "reason": "Criteria are explicit"}), []
    )
    verdict = await critic.validate_anchor_structure(anchor)
    assert verdict.status == "ok"
    assert verdict.confidence == 0.92
    assert mock_client.chat.call_args[0][0] == "fast"

@pytest.mark.asyncio
async def test_validate_anchor_structure_blocked(critic, anchor, mock_client):
    mock_client.chat.return_value = (
        json.dumps({"status": "blocked", "confidence": 0.85, "reason": "Criteria are vague"}), []
    )
    verdict = await critic.validate_anchor_structure(anchor)
    assert verdict.status == "blocked"

@pytest.mark.asyncio
async def test_validate_anchor_structure_escalates_on_low_confidence(critic, anchor, mock_client):
    mock_client.chat.side_effect = [
        (json.dumps({"status": "ok", "confidence": 0.50, "reason": "maybe"}), []),
        (json.dumps({"status": "revise", "confidence": 0.80, "reason": "needs work"}), []),
    ]
    verdict = await critic.validate_anchor_structure(anchor)
    assert mock_client.chat.call_count == 2
    assert mock_client.chat.call_args_list[1][0][0] == "reasoner"

@pytest.mark.asyncio
async def test_verify_step_ok(critic, anchor, low_risk_trace, mock_client):
    from app.core.task_store import Task
    step = Task(goal="search web", expected_outcome="URLs returned")
    mock_client.chat.return_value = (
        json.dumps({"status": "ok", "confidence": 0.87, "reason": "Step output matches"}), []
    )
    verdict = await critic.verify_step(anchor, step, [low_risk_trace])
    assert verdict.status == "ok"

@pytest.mark.asyncio
async def test_verify_step_uses_reasoner_for_high_risk_trace(critic, anchor, high_risk_trace, mock_client):
    from app.core.task_store import Task
    step = Task(goal="delete files", expected_outcome="Files removed")
    mock_client.chat.return_value = (
        json.dumps({"status": "ok", "confidence": 0.82, "reason": "ok"}), []
    )
    await critic.verify_step(anchor, step, [high_risk_trace])
    assert mock_client.chat.call_args[0][0] == "reasoner"

@pytest.mark.asyncio
async def test_quick_anchor_check_uses_fast_model(critic, anchor, low_risk_trace, mock_client):
    mock_client.chat.return_value = (
        json.dumps({"status": "ok", "confidence": 0.80, "reason": "ok", "goal_progress_delta": 0.4}), []
    )
    verdict = await critic.quick_anchor_check(anchor, [low_risk_trace])
    assert verdict.status == "ok"
    assert verdict.goal_progress_delta == 0.4
    assert mock_client.chat.call_args[0][0] == "fast"

@pytest.mark.asyncio
async def test_validate_anchor_satisfaction_all_low_risk_uses_fast(critic, anchor, low_risk_trace, mock_client):
    mock_client.chat.return_value = (
        json.dumps({"status": "ok", "confidence": 0.91, "reason": "All satisfied"}), []
    )
    verdict = await critic.validate_anchor_satisfaction(anchor, [low_risk_trace], "Results here")
    assert verdict.status == "ok"
    for call in mock_client.chat.call_args_list:
        assert call[0][0] == "fast"

@pytest.mark.asyncio
async def test_validate_anchor_satisfaction_high_risk_trace_uses_reasoner(
    critic, anchor, high_risk_trace, mock_client
):
    mock_client.chat.return_value = (
        json.dumps({"status": "ok", "confidence": 0.88, "reason": "ok"}), []
    )
    await critic.validate_anchor_satisfaction(anchor, [high_risk_trace], "Done.")
    calls = [c[0][0] for c in mock_client.chat.call_args_list]
    assert "reasoner" in calls

@pytest.mark.asyncio
async def test_verdict_oscillation_forces_reasoner(critic, anchor, low_risk_trace, mock_client):
    from app.core.task_store import Task
    step = Task(goal="search", expected_outcome="results", id="fixed-step-id")

    mock_client.chat.side_effect = [
        (json.dumps({"status": "ok", "confidence": 0.80, "reason": "ok"}), []),
        (json.dumps({"status": "revise", "confidence": 0.80, "reason": "needs work"}), []),
        (json.dumps({"status": "revise", "confidence": 0.85, "reason": "confirmed revise"}), []),
    ]

    v1 = await critic.verify_step(anchor, step, [low_risk_trace])
    v2 = await critic.verify_step(anchor, step, [low_risk_trace])

    assert v1.status == "ok"
    assert v2.status == "revise"
    assert mock_client.chat.call_args_list[2][0][0] == "reasoner"

@pytest.mark.asyncio
async def test_validate_anchor_satisfaction_phase1_issues_force_reasoner(
    critic, anchor, low_risk_trace, mock_client
):
    mock_client.chat.side_effect = [
        (json.dumps({"status": "revise", "confidence": 0.70, "reason": "missing artifact"}), []),
        (json.dumps({"status": "revise", "confidence": 0.80, "reason": "criteria unmet"}), []),
    ]
    verdict = await critic.validate_anchor_satisfaction(
        anchor, [low_risk_trace], "Partial results only"
    )
    assert verdict.status == "revise"
    calls = [c[0][0] for c in mock_client.chat.call_args_list]
    assert "reasoner" in calls, f"Expected 'reasoner' in model calls, got: {calls}"