from __future__ import annotations

import json
import asyncio
import logging

from typing import Literal, cast 
from dataclasses import dataclass, field

from app.services.inference_client import InferenceClient

logger = logging.getLogger("wade.critic")

ACCEPT_THRESHOLD    = 0.68
ESCALATE_THRESHOLD  = 0.55
CRITIC_K            = 5

_HIGH_RISK_KEYWORDS = (
    "delete", "remove", "shell", "execute", "run script",
    "send message", "schedule", "reset db", "arm", "disarm",
)

CriticStatus = Literal["ok", "revise", "suspect", "blocked"]

@dataclass
class GoalAnchor:
    original_goal:    str
    success_criteria: list[str]
    constraints:      list[str]

@dataclass
class ToolTrace:
    tool_name:        str
    args_summary:     str
    result_summary:   str
    risk:             str              # "low" | "medium" | "high"
    exit_status:      str              # "success" | "error" | "loop_detected" | "timeout"
    duration_ms:      int
    was_retried:      bool             = False
    intent_alignment: float | None     = None

@dataclass
class CriticVerdict:
    status:              CriticStatus
    confidence:          float
    reason:              str | None        = None
    revised_steps:       list[dict] | None = None
    surface_to_user:     bool              = False
    goal_progress_delta: float | None      = None

def _parse_verdict(text: str, default_status: CriticStatus = "ok") -> CriticVerdict:
    raw = text.strip()
    import re
    m = re.search(r"```(?:json)?\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    else:
        first_brace = raw.find("{")
        last_brace = raw.rfind("}")
        first_bracket = raw.find("[")
        last_bracket = raw.rfind("]")

        starts = [i for i in [first_brace, first_bracket] if i != -1]
        ends = [i for i in [last_brace, last_bracket] if i != -1]

        if starts and ends:
            start = min(starts)
            end = max(ends) + 1
            if start < end:
                raw = raw[start:end]
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")

        parsed_status = data.get("status", default_status)
        if parsed_status not in {"ok", "revise", "suspect", "blocked"}:
            parsed_status = default_status
            
        return CriticVerdict(
            status=cast(CriticStatus, parsed_status),
            confidence=float(data.get("confidence", 0.5)),
            reason=data.get("reason"),
            revised_steps=data.get("revised_steps"),
            surface_to_user=bool(data.get("surface_to_user", False)),
            goal_progress_delta=data.get("goal_progress_delta"),
        )
    except (json.JSONDecodeError, ValueError):
        logger.warning("[CRITIC] Failed to parse verdict: %s", text[:200])
        return CriticVerdict(status=default_status, confidence=0.5, reason="parse_error")

class CriticAgent:
    """Stateful constraint system that validates plans and execution traces."""

    def __init__(self, client: InferenceClient) -> None:
        self._client = client
        self._step_verdicts: dict[str, list[CriticVerdict]] = {}

    def reset_step_verdicts(self) -> None:
        self._step_verdicts.clear()

    async def _call(
        self,
        model: str,
        prompt: str,
        step_key: str | None = None,
    ) -> CriticVerdict:
        messages = [{"role": "user", "content": prompt}]
        raw, _ = await self._client.chat(model, messages, json_format=True)
        verdict = _parse_verdict(raw)

        if step_key:
            history = self._step_verdicts.setdefault(step_key, [])
            history.append(verdict)
            if len(history) >= 2 and history[-2].status != history[-1].status:
                logger.warning("[CRITIC] Oscillation on step %s — forcing reasoner", step_key)
                raw2, _ = await self._client.chat("reasoner", messages, json_format=True)
                resolved = _parse_verdict(raw2)
                history.append(resolved)
                return resolved

        return verdict

    async def _call_with_escalation(
        self,
        prompt: str,
        initial_model: str = "fast",
        step_key: str | None = None,
    ) -> CriticVerdict:
        verdict = await self._call(initial_model, prompt, step_key=step_key)

        if initial_model != "reasoner" and verdict.confidence < ACCEPT_THRESHOLD:
            messages = [{"role": "user", "content": prompt}]
            raw2, _ = await self._client.chat("reasoner", messages, json_format=True)
            escalated = _parse_verdict(raw2)
            if step_key:
                self._step_verdicts.setdefault(step_key, []).append(escalated)
            if verdict.confidence < ESCALATE_THRESHOLD and escalated.confidence < ESCALATE_THRESHOLD:
                return CriticVerdict(
                    status=escalated.status,
                    confidence=escalated.confidence,
                    reason=f"[uncertain] {escalated.reason or ''}",
                    revised_steps=escalated.revised_steps,
                    surface_to_user=True,
                )
            return escalated

        return verdict

    async def validate_anchor_structure(self, anchor: GoalAnchor) -> CriticVerdict:
        """Fast model. Checks success_criteria are explicit, testable, non-contradictory."""
        criteria_block = "\n".join(f"- {c}" for c in anchor.success_criteria) or "(none)"
        constraints_block = "\n".join(f"- {c}" for c in anchor.constraints) or "(none)"
        prompt = (
            f"You are a plan critic. Evaluate whether these success criteria are well-formed.\n\n"
            f"Goal: {anchor.original_goal}\n\n"
            f"Success criteria:\n{criteria_block}\n\n"
            f"Constraints:\n{constraints_block}\n\n"
            f"For each criterion, check:\n"
            f"1. Is it explicit? (references a concrete artifact, state, or output)\n"
            f"2. Is it testable? (can be confirmed from a result string or tool output)\n"
            f"3. Is it non-contradictory with other criteria?\n\n"
            f"Return JSON only:\n"
            f'{{"status": "ok"|"revise"|"blocked", "confidence": 0.0-1.0, '
            f'"reason": "...", "revised_criteria": [...] or null}}\n\n'
            f'- "ok": all criteria well-formed\n'
            f'- "revise": some need fixing (provide revised_criteria)\n'
            f'- "blocked": so vague/contradictory that execution cannot be verified'
        )
        return await self._call_with_escalation(prompt, initial_model="fast")

    async def validate_plan(
        self, anchor: GoalAnchor, steps: list
    ) -> CriticVerdict:
        """Two-pass plan validation with anchor perturbation check."""
        steps_block = "\n".join(
            f"{i+1}. {s.goal} → expected: {s.expected_outcome or '(unspecified)'}"
            for i, s in enumerate(steps)
        )
        criteria_block = "\n".join(f"- {c}" for c in anchor.success_criteria)
        constraints_block = "\n".join(f"- {c}" for c in anchor.constraints) or "(none)"

        pass1_prompt = (
            f"You are a plan critic. Evaluate this plan against the goal.\n\n"
            f"Goal: {anchor.original_goal}\n\n"
            f"Success criteria:\n{criteria_block}\n\n"
            f"Constraints:\n{constraints_block}\n\n"
            f"Steps:\n{steps_block}\n\n"
            f"Check:\n"
            f"1. Do the steps collectively satisfy all success criteria?\n"
            f"2. Are constraints respected across all steps?\n"
            f"3. Are there missing or redundant steps?\n"
            f"4. Does each step have a plausible expected outcome?\n\n"
            f"Return JSON only:\n"
            f'{{"status": "ok"|"revise"|"blocked", "confidence": 0.0-1.0, '
            f'"reason": "...", "revised_steps": [...] or null}}'
        )

        pass2_prompt = (
            f"Re-evaluate this plan assuming the goal is interpreted strictly literally. "
            f"Flag any step that relies on an assumption not explicitly stated in the goal.\n\n"
            f"Goal: {anchor.original_goal}\n\n"
            f"Steps:\n{steps_block}\n\n"
            f"Return JSON only:\n"
            f'{{"status": "ok"|"revise"|"blocked", "confidence": 0.0-1.0, '
            f'"reason": "..."}}'
        )

        pass1_model = "fast"
        for s in steps:
            if any(kw in s.goal.lower() for kw in _HIGH_RISK_KEYWORDS):
                pass1_model = "reasoner"
                break

        v1, v2 = await asyncio.gather(
            self._call_with_escalation(pass1_prompt, initial_model=pass1_model),
            self._call_with_escalation(pass2_prompt, initial_model="fast"),
        )

        if v1.status != v2.status:
            combined_prompt = (
                f"Two critics disagree on this plan. Provide a final verdict.\n\n"
                f"Critic 1 ({v1.status}, {v1.confidence:.2f}): {v1.reason}\n"
                f"Critic 2 ({v2.status}, {v2.confidence:.2f}): {v2.reason}\n\n"
                f"Goal: {anchor.original_goal}\nSteps:\n{steps_block}\n\n"
                f"Return JSON only:\n"
                f'{{"status": "ok"|"revise"|"blocked", "confidence": 0.0-1.0, '
                f'"reason": "...", "revised_steps": [...] or null}}'
            )
            raw, _ = await self._client.chat("reasoner", [{"role": "user", "content": combined_prompt}], json_format=True)
            return _parse_verdict(raw)

        return v1

    async def verify_step(
        self, anchor: GoalAnchor, step, traces: list[ToolTrace]
    ) -> CriticVerdict:
        """Post-step verification. Model = max risk across traces."""
        max_risk = "low"
        for t in traces:
            if t.risk == "high":
                max_risk = "high"
                break
            if t.risk == "medium":
                max_risk = "medium"

        model = "reasoner" if max_risk == "high" else "fast"

        traces_block = "\n".join(
            f"- {t.tool_name} ({t.risk} risk, {t.exit_status}, {t.duration_ms}ms): {t.result_summary}"
            for t in traces
        ) or "(no tool calls)"

        prompt = (
            f"You are a plan critic. Evaluate whether this step completed correctly.\n\n"
            f"Overall goal: {anchor.original_goal}\n\n"
            f"Step goal: {step.goal}\n"
            f"Step expected outcome: {step.expected_outcome or '(unspecified)'}\n\n"
            f"Tool calls made:\n{traces_block}\n\n"
            f"Check:\n"
            f"1. Did the step produce an output consistent with the expected outcome?\n"
            f"2. Were the right tools used for this step?\n"
            f"3. Are there signs of error, timeout, or loop?\n\n"
            f"Return JSON only:\n"
            f'{{"status": "ok"|"revise"|"blocked", "confidence": 0.0-1.0, '
            f'"reason": "...", "goal_progress_delta": -1.0 to 1.0 or null}}'
        )
        return await self._call_with_escalation(prompt, initial_model=model, step_key=step.id)

    async def quick_anchor_check(
        self, anchor: GoalAnchor, partial_traces: list[ToolTrace]
    ) -> CriticVerdict:
        """Lightweight mid-pipeline drift guard. Always uses fast model."""
        criteria_block = "\n".join(f"- {c}" for c in anchor.success_criteria)
        traces_block = "\n".join(
            f"- {t.tool_name} ({t.risk}): {t.result_summary}"
            for t in partial_traces[-10:]
        ) or "(none)"

        prompt = (
            f"You are a plan critic performing a mid-execution drift check.\n\n"
            f"Goal: {anchor.original_goal}\n\n"
            f"Success criteria:\n{criteria_block}\n\n"
            f"Recent tool calls (most recent last):\n{traces_block}\n\n"
            f"Is the trajectory so far consistent with reaching the success criteria? "
            f"Rate progress toward goal and identify any drift.\n\n"
            f"Return JSON only:\n"
            f'{{"status": "ok"|"revise"|"suspect"|"blocked", "confidence": 0.0-1.0, '
            f'"reason": "...", "goal_progress_delta": -1.0 to 1.0}}'
        )
        raw, _ = await self._client.chat("fast", [{"role": "user", "content": prompt}], json_format=True)
        return _parse_verdict(raw)

    async def validate_anchor_satisfaction(
        self,
        anchor: GoalAnchor,
        all_traces: list[ToolTrace],
        final_output: str,
    ) -> CriticVerdict:
        """Terminal truth gate. Phase 1: structural (fast). Phase 2: satisfaction (risk-driven)."""
        has_nontrivial_risk = any(t.risk in ("medium", "high") for t in all_traces)
        satisfaction_model = "reasoner" if has_nontrivial_risk else "fast"

        criteria_block = "\n".join(f"- {c}" for c in anchor.success_criteria)

        phase1_prompt = (
            f"You are a plan critic. Check whether the execution produced outputs matching "
            f"the shape of the success criteria (files exist, responses returned, no crash).\n\n"
            f"Success criteria:\n{criteria_block}\n\n"
            f"Final output (first 500 chars):\n{final_output[:500]}\n\n"
            f"Return JSON only:\n"
            f'{{"status": "ok"|"revise"|"blocked", "confidence": 0.0-1.0, "reason": "..."}}'
        )
        phase1_raw, _ = await self._client.chat("fast", [{"role": "user", "content": phase1_prompt}], json_format=True)
        phase1 = _parse_verdict(phase1_raw)

        if phase1.status == "blocked" and phase1.confidence >= ACCEPT_THRESHOLD:
            return phase1

        if phase1.status != "ok":
            satisfaction_model = "reasoner"

        traces_block = "\n".join(
            f"- {t.tool_name} ({t.risk}): {t.result_summary}"
            for t in all_traces
        ) or "(none)"

        phase2_prompt = (
            f"You are a plan critic performing the final goal satisfaction check.\n\n"
            f"Original goal: {anchor.original_goal}\n\n"
            f"Success criteria:\n{criteria_block}\n\n"
            f"All tool calls made:\n{traces_block}\n\n"
            f"Final output (first 800 chars):\n{final_output[:800]}\n\n"
            f"Does the actual output satisfy EVERY item in the success criteria?\n\n"
            f"Return JSON only:\n"
            f'{{"status": "ok"|"revise"|"blocked", "confidence": 0.0-1.0, '
            f'"reason": "...", "goal_progress_delta": -1.0 to 1.0}}'
        )
        phase2_raw, _ = await self._client.chat(
            satisfaction_model, [{"role": "user", "content": phase2_prompt}], json_format=True
        )
        return _parse_verdict(phase2_raw)