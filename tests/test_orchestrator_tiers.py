from __future__ import annotations

import asyncio
import pytest

from unittest.mock import AsyncMock, MagicMock, patch, call

from app.core.orchestrator import Orchestrator
from app.core.task_store import Task, TaskStatus, TaskStore
from app.agents.critic import CriticVerdict, GoalAnchor, ACCEPT_THRESHOLD

async def _aiter(*items):
    """Async generator that yields the given items."""
    for item in items:
        yield item

def _ok_verdict(confidence: float = 0.9) -> CriticVerdict:
    return CriticVerdict(status="ok", confidence=confidence, reason="")

def _blocked_verdict(confidence: float = 0.9, reason: str = "bad") -> CriticVerdict:
    return CriticVerdict(status="blocked", confidence=confidence, reason=reason)

def _make_anchor(goal: str = "do stuff") -> GoalAnchor:
    return GoalAnchor(
        original_goal=goal,
        success_criteria=["criterion 1"],
        constraints=[],
    )

def _make_subtask(goal: str = "sub step") -> Task:
    return Task(goal=goal)

class MockExecutor:
    """Minimal executor that yields 'result' and exposes empty traces."""

    def __init__(self, client, tier_ctx=None):
        self._client = client
        self.traces = []

    async def execute(self, task, session_id=None, conv_id=None, sender_facts_dir=None):
        yield "result"

class MockExecutorCls:
    """Class factory wrapper so set_executor_cls() works correctly."""
    def __new__(cls, client, tier_ctx=None):
        return MockExecutor(client, tier_ctx=tier_ctx)

def _make_orchestrator():
    """Build a bare Orchestrator with an in-memory task store and no real clients."""
    store = MagicMock(spec=TaskStore)
    store.get.return_value = None
    client = MagicMock()
    client.complete = MagicMock(side_effect=lambda model, messages: _aiter("synth"))

    orch = Orchestrator(task_store=store, inference_client=client)
    orch.set_executor_cls(MockExecutorCls)
    return orch

def _make_planner(anchor, subtasks, needs_planning: bool = True):
    planner = MagicMock()
    planner.needs_planning.return_value = needs_planning
    planner.decompose = AsyncMock(return_value=(anchor, subtasks))
    return planner


async def _collect(agen):
    """Drain an async generator into a list of strings."""
    chunks = []
    async for chunk in agen:
        chunks.append(chunk)
    return chunks

@pytest.mark.asyncio
async def test_medium_skips_validate_plan_and_satisfaction():
    anchor = _make_anchor()
    subtasks = [_make_subtask("step 1"), _make_subtask("step 2")]

    orch = _make_orchestrator()
    orch.set_planner(_make_planner(anchor, subtasks))

    orch._critic.validate_anchor_structure = AsyncMock(return_value=_ok_verdict())
    orch._critic.validate_plan = AsyncMock(return_value=_ok_verdict())
    orch._critic.validate_anchor_satisfaction = AsyncMock(return_value=_ok_verdict())
    orch._critic.reset_step_verdicts = MagicMock()
    orch._critic.verify_step = AsyncMock(return_value=_ok_verdict())

    with patch("app.core.orchestrator.classify", return_value="medium"), \
         patch("app.core.orchestrator._check_connectivity", return_value=True):
        chunks = await _collect(orch._execute_task(Task(goal="summarize docs")))

    output = "".join(chunks)

    orch._critic.validate_plan.assert_not_called()
    orch._critic.validate_anchor_satisfaction.assert_not_called()
    calls = orch._client.complete.call_args_list  # type: ignore
    models_used = [c.args[0] for c in calls if c.args]
    assert "chat" in models_used, f"Expected 'chat' synthesis, got: {models_used}"
    assert "reasoner" not in models_used, f"Unexpected 'reasoner' in: {models_used}"

@pytest.mark.asyncio
async def test_complex_calls_validate_plan_and_satisfaction():
    anchor = _make_anchor()
    subtasks = [_make_subtask("step 1"), _make_subtask("step 2")]

    orch = _make_orchestrator()
    orch.set_planner(_make_planner(anchor, subtasks))

    orch._critic.validate_anchor_structure = AsyncMock(return_value=_ok_verdict())
    orch._critic.validate_plan = AsyncMock(return_value=_ok_verdict())
    orch._critic.validate_anchor_satisfaction = AsyncMock(return_value=_ok_verdict())
    orch._critic.reset_step_verdicts = MagicMock()
    orch._critic.verify_step = AsyncMock(return_value=_ok_verdict())

    with patch("app.core.orchestrator.classify", return_value="complex"), \
         patch("app.core.orchestrator._check_connectivity", return_value=True):
        chunks = await _collect(orch._execute_task(Task(goal="do a complex thing")))

    orch._critic.validate_plan.assert_called_once()
    orch._critic.validate_anchor_satisfaction.assert_called_once()

@pytest.mark.asyncio
async def test_medium_failup_on_blocked_anchor():
    anchor = _make_anchor()
    subtasks = [_make_subtask("step 1"), _make_subtask("step 2")]

    orch = _make_orchestrator()
    planner = _make_planner(anchor, subtasks)
    orch.set_planner(planner)

    orch._critic.validate_anchor_structure = AsyncMock(
        side_effect=[_blocked_verdict(), _ok_verdict()]
    )
    orch._critic.validate_plan = AsyncMock(return_value=_ok_verdict())
    orch._critic.validate_anchor_satisfaction = AsyncMock(return_value=_ok_verdict())
    orch._critic.reset_step_verdicts = MagicMock()
    orch._critic.verify_step = AsyncMock(return_value=_ok_verdict())

    with patch("app.core.orchestrator.classify", return_value="medium"), \
         patch("app.core.orchestrator._check_connectivity", return_value=True):
        chunks = await _collect(orch._execute_task(Task(goal="medium task")))

    output = "".join(chunks)

    assert planner.decompose.await_count == 2, (
        f"Expected decompose called 2 times, got {planner.decompose.await_count}"
    )

    orch._critic.validate_plan.assert_called_once()
    orch._critic.validate_anchor_satisfaction.assert_called_once()

@pytest.mark.asyncio
async def test_medium_double_block_surfaces_error():
    anchor = _make_anchor()
    subtasks = [_make_subtask("step 1")]

    orch = _make_orchestrator()
    orch.set_planner(_make_planner(anchor, subtasks))

    orch._critic.validate_anchor_structure = AsyncMock(
        side_effect=[
            _blocked_verdict(confidence=0.95, reason="structurally invalid"),
            _blocked_verdict(confidence=0.95, reason="still invalid after re-decompose"),
        ]
    )
    orch._critic.reset_step_verdicts = MagicMock()
    orch._critic.validate_plan = AsyncMock(return_value=_ok_verdict())

    with patch("app.core.orchestrator.classify", return_value="medium"), \
         patch("app.core.orchestrator._check_connectivity", return_value=True):
        chunks = await _collect(orch._execute_task(Task(goal="bad medium task")))

    output = "".join(chunks)

    assert "blocked" in output, f"Expected 'blocked' in output, got: {repr(output)}"

    update_calls = [str(c) for c in orch._store.update_status.call_args_list]  # type: ignore
    statuses_set = [c.args[1] for c in orch._store.update_status.call_args_list if c.args]  # type: ignore
    assert TaskStatus.INVALID_PLAN in statuses_set, (
        f"Expected INVALID_PLAN in status calls, got: {statuses_set}"
    )

@pytest.mark.asyncio
async def test_path_containing_goal_promotes_to_complex():
    """A goal with a path-like string should be promoted to complex even if classify() returns medium."""
    anchor = _make_anchor()
    subtasks = [_make_subtask("step 1"), _make_subtask("step 2")]

    orch = _make_orchestrator()
    orch.set_planner(_make_planner(anchor, subtasks))

    orch._critic.validate_anchor_structure = AsyncMock(return_value=_ok_verdict())
    orch._critic.validate_plan = AsyncMock(return_value=_ok_verdict())
    orch._critic.validate_anchor_satisfaction = AsyncMock(return_value=_ok_verdict())
    orch._critic.reset_step_verdicts = MagicMock()
    orch._critic.verify_step = AsyncMock(return_value=_ok_verdict())

    with patch("app.core.orchestrator.classify", return_value="medium"), \
         patch("app.core.orchestrator._check_connectivity", return_value=True):
        chunks = await _collect(orch._execute_task(Task(goal="analyze /var/log/syslog")))

    orch._critic.validate_plan.assert_called_once()
    orch._critic.validate_anchor_satisfaction.assert_called_once()