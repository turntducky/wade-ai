from __future__ import annotations


import json
import pytest
import asyncio

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.agents.critic import CriticAgent, CriticVerdict, GoalAnchor

def _verdict_json(status: str, confidence: float = 0.9, reason: str = "ok") -> str:
    return json.dumps({"status": status, "confidence": confidence, "reason": reason})

def _make_verdict(status: str, confidence: float = 0.9) -> CriticVerdict:
    return CriticVerdict(status=status, confidence=confidence, reason="ok")  # type: ignore[arg-type]

@dataclass
class FakeStep:
    goal: str = "do something"
    expected_outcome: str = "done"

@pytest.fixture
def anchor() -> GoalAnchor:
    return GoalAnchor(
        original_goal="Write a report",
        success_criteria=["Report written"],
        constraints=["No external calls"],
    )

@pytest.fixture
def steps() -> list[FakeStep]:
    return [FakeStep(goal="draft report", expected_outcome="draft.md exists")]

@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.chat = AsyncMock()
    return client


@pytest.fixture
def critic(mock_client: MagicMock) -> CriticAgent:
    return CriticAgent(mock_client)


async def test_validate_plan_passes_run_concurrently(critic: CriticAgent, anchor: GoalAnchor, steps: list[FakeStep]) -> None:
    """Verify that the two _call_with_escalation invocations are started concurrently."""
    events: list[tuple[str, int]] = []

    async def fake_call(prompt: str, *, initial_model: str = "fast", step_key: str | None = None) -> CriticVerdict:
        idx = len([e for e in events if e[0] == "start"])
        events.append(("start", idx))
        await asyncio.sleep(0)
        events.append(("end", idx))
        return _make_verdict("ok")

    with patch.object(critic, "_call_with_escalation", side_effect=fake_call):
        await critic.validate_plan(anchor, steps)

    starts = [e for e in events if e[0] == "start"]
    ends = [e for e in events if e[0] == "end"]

    assert len(starts) == 2, f"Expected 2 starts, got {starts}"
    assert len(ends) == 2, f"Expected 2 ends, got {ends}"

    start_positions = [i for i, e in enumerate(events) if e[0] == "start"]
    end_positions = [i for i, e in enumerate(events) if e[0] == "end"]

    assert max(start_positions) < min(end_positions), (
        f"Passes ran sequentially! Event order: {events}"
    )

async def test_validate_plan_no_tiebreaker_when_passes_agree(critic: CriticAgent, anchor: GoalAnchor, steps: list[FakeStep], mock_client: MagicMock) -> None:
    """When v1 and v2 have the same status, chat("reasoner", ...) must NOT be called."""
    agreed_verdict = _make_verdict("ok", confidence=0.9)

    with patch.object(
        critic,
        "_call_with_escalation",
        new=AsyncMock(return_value=agreed_verdict),
    ):
        result = await critic.validate_plan(anchor, steps)

    mock_client.chat.assert_not_called()
    assert result.status == "ok"

async def test_validate_plan_tiebreaker_fires_when_passes_disagree(critic: CriticAgent, anchor: GoalAnchor, steps: list[FakeStep], mock_client: MagicMock) -> None:
    """When v1='approved' and v2='blocked' (different statuses), the tiebreaker must fire."""
    v1 = _make_verdict("ok", confidence=0.9)
    v2 = _make_verdict("blocked", confidence=0.8)

    call_count = 0

    async def side_effect(prompt: str, *, initial_model: str = "fast", step_key: str | None = None) -> CriticVerdict:
        nonlocal call_count
        result = v1 if call_count == 0 else v2
        call_count += 1
        return result

    mock_client.chat.return_value = (
        _verdict_json("revise", confidence=0.85, reason="tiebreaker says revise"),
        [],
    )

    with patch.object(critic, "_call_with_escalation", side_effect=side_effect):
        result = await critic.validate_plan(anchor, steps)

    assert mock_client.chat.call_count == 1, (
        f"Expected tiebreaker chat call count=1, got {mock_client.chat.call_count}"
    )
    tiebreaker_model = mock_client.chat.call_args[0][0]
    assert tiebreaker_model == "reasoner", (
        f"Expected tiebreaker to use 'reasoner', got '{tiebreaker_model}'"
    )
    assert result.status == "revise"