from __future__ import annotations

import json
import pytest
import pytest_asyncio

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.planner import PlannerAgent
from app.core.telemetry import TelemetryStore
from app.core.orchestrator import Orchestrator
from app.core.task_store import Task, TaskStore, TaskStatus
from app.agents.critic import CriticVerdict, GoalAnchor, ToolTrace

async def _drain(gen) -> str:
    """Collect all chunks from an async generator."""
    parts = []
    async for chunk in gen:
        parts.append(chunk)
    return "".join(parts)

def _make_inference_client(chat_response: str = '{"status":"ok","confidence":0.9,"reason":"ok"}'):
    """Mock InferenceClient whose chat() returns chat_response and complete() yields one chunk."""
    client = MagicMock()
    client.chat = AsyncMock(return_value=(chat_response, {}))

    async def _complete(model, messages, **kwargs):
        yield "Final synthesized result."

    client.complete = MagicMock(side_effect=_complete)
    return client

class _MockExecutor:
    """Minimal executor stub: yields one chunk, exposes empty traces."""

    def __init__(self, client, tier_ctx=None):
        self.traces: list[ToolTrace] = []

    async def execute(self, task: Task, session_id=None, conv_id=None, sender_facts_dir=None):
        yield f"[mock result for: {task.goal[:60]}]"

@pytest.fixture()
def isolated_stores(tmp_path: Path):
    task_store = TaskStore(tmp_path / "tasks.db")
    telemetry = TelemetryStore(tmp_path / "telemetry.db")
    return task_store, telemetry

@pytest.fixture()
def orchestrator_simple(isolated_stores):
    """Orchestrator wired for simple (no-planning) goals."""
    task_store, telemetry = isolated_stores
    client = _make_inference_client()
    orch = Orchestrator(task_store=task_store, inference_client=client)
    orch.set_executor_cls(_MockExecutor)
    orch.set_telemetry(telemetry)
    return orch, task_store, telemetry

def _make_planning_orchestrator(isolated_stores, anchor_verdict: CriticVerdict, plan_verdict: CriticVerdict | None = None):
    """Orchestrator wired with a mock planner and fully mocked critic."""
    task_store, telemetry = isolated_stores

    client = _make_inference_client()
    orch = Orchestrator(task_store=task_store, inference_client=client)
    orch.set_executor_cls(_MockExecutor)
    orch.set_telemetry(telemetry)

    goal_sentinel = "research topic X, then summarize findings, then save a final report"  # triggers complex tier

    subtasks = [
        Task(goal="Step 1: gather data", created_by="planner"),
        Task(goal="Step 2: process results", created_by="planner"),
        Task(goal="Step 3: write report", created_by="planner"),
    ]
    anchor = GoalAnchor(
        original_goal=goal_sentinel,
        success_criteria=["data gathered", "results processed", "report written"],
        constraints=[],
    )

    mock_planner = MagicMock()
    mock_planner.needs_planning = MagicMock(return_value=True)
    mock_planner.decompose = AsyncMock(return_value=(anchor, subtasks))
    orch.set_planner(mock_planner)

    orch._critic.validate_anchor_structure = AsyncMock(return_value=anchor_verdict)
    ok_verdict = CriticVerdict(status="ok", confidence=0.95, reason="looks good")
    orch._critic.validate_plan = AsyncMock(return_value=plan_verdict or ok_verdict)
    orch._critic.verify_step = AsyncMock(return_value=ok_verdict)
    orch._critic.quick_anchor_check = AsyncMock(return_value=ok_verdict)
    orch._critic.validate_anchor_satisfaction = AsyncMock(return_value=ok_verdict)

    return orch, task_store, telemetry, goal_sentinel

@pytest.mark.asyncio
async def test_simple_tool_call(orchestrator_simple):
    """A simple goal bypasses the planner and reaches COMPLETED status."""
    orch, task_store, telemetry = orchestrator_simple

    with patch("app.core.orchestrator._check_connectivity", return_value=True):
        output = await _drain(orch.process("what time is it", is_system=True))

    assert "[mock result for:" in output

    recent = task_store.list_recent(limit=5)
    assert recent, "Expected at least one task in store"
    assert recent[0].status == TaskStatus.COMPLETED

    verdicts = telemetry.get_verdicts(recent[0].id)
    assert verdicts == [], f"Expected no verdicts on simple path, got {verdicts}"


@pytest.mark.asyncio
async def test_multi_step_plan_logs_verdicts(isolated_stores):
    """A complex goal triggers planning, wave execution, and TelemetryStore verdict logging."""
    anchor_ok = CriticVerdict(status="ok", confidence=0.92, reason="criteria are explicit")
    plan_ok = CriticVerdict(status="ok", confidence=0.91, reason="plan covers all criteria")
    orch, task_store, telemetry, goal = _make_planning_orchestrator(
        isolated_stores,
        anchor_verdict=anchor_ok,
        plan_verdict=plan_ok,
    )
    with patch("app.core.orchestrator._check_connectivity", return_value=True):
        output = await _drain(orch.process(goal, is_system=True))

    assert "Final synthesized result." in output

    recent = task_store.list_recent(limit=10)
    root = next((t for t in recent if t.goal == goal), None)
    assert root is not None, "Root task not found in store"
    assert root.status == TaskStatus.COMPLETED, f"Expected COMPLETED, got {root.status}"

    verdicts = telemetry.get_verdicts(root.id)
    check_types = {v["check_type"] for v in verdicts}
    assert "anchor_structure" in check_types, "Missing anchor_structure verdict"
    assert "plan" in check_types, "Missing plan verdict"
    assert "step" in check_types, "Missing step verdict"
    assert "satisfaction" in check_types, "Missing satisfaction verdict"

    step_verdicts = [v for v in verdicts if v["check_type"] == "step"]
    assert len(step_verdicts) == 3, f"Expected 3 step verdicts, got {len(step_verdicts)}"

@pytest.mark.asyncio
async def test_critic_blocks_plan(isolated_stores):
    """Critic returning 'blocked' with high confidence stops execution and marks INVALID_PLAN."""
    blocked_verdict = CriticVerdict(
        status="blocked",
        confidence=0.93,
        reason="Goal involves destructive irreversible operations with no recovery path.",
        surface_to_user=True,
    )
    orch, task_store, telemetry, goal = _make_planning_orchestrator(
        isolated_stores,
        anchor_verdict=blocked_verdict,
    )

    with patch("app.core.orchestrator._check_connectivity", return_value=True):
        output = await _drain(orch.process(goal, is_system=True))

    assert "blocked" in output.lower()

    recent = task_store.list_recent(limit=10)
    root = next((t for t in recent if t.goal == goal), None)
    assert root is not None, "Root task not found in store"
    assert root.status == TaskStatus.INVALID_PLAN, (
        f"Expected INVALID_PLAN after critic block, got {root.status}"
    )

    verdicts = telemetry.get_verdicts(root.id)
    assert verdicts, "Expected at least one verdict to be persisted"
    anchor_v = next((v for v in verdicts if v["check_type"] == "anchor_structure"), None)
    assert anchor_v is not None, "anchor_structure verdict missing from telemetry"
    assert anchor_v["status"] == "blocked"
    assert anchor_v["confidence"] >= 0.68

    step_verdicts = [v for v in verdicts if v["check_type"] == "step"]
    assert step_verdicts == [], f"Executor should not have run, but got step verdicts: {step_verdicts}"
