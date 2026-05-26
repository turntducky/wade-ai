"""
Cognitive Layer Isolation — Phase 5 of the architectural specification.

The LLM is a probabilistic proposal generator. It is not an agent.
It has no authority over state. It receives a normalized view of the world
and returns a strictly typed ActionProposal. Nothing else crosses the boundary.

Isolation guarantees:
- The LLM never sees raw system logs, Python tracebacks, or error strings.
- The LLM never sees internal sequence IDs, hash values, or ledger internals.
- The LLM never sees the full ActionType enum — only the legal subset for its tier.
- If a proposal is denied, the LLM receives a new NormalizedState reflecting
  the updated constraint set. It does NOT interpret the denial.
- CognitionInput is the only permissible entry point into the LLM boundary.
- ActionProposal is the only permissible exit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from app.core.runtime.schemas import (
    ActionProposal, ActionType, SystemSnapshot, SideEffectStatus,
)
from app.core.runtime.policy import Capability, get_legal_actions

logger = logging.getLogger("wade.runtime.cognition")


# ── Error Code Normaliser ─────────────────────────────────────────────────────

_ERROR_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("permission", "denied", "forbidden", "unauthorized"), "ERR_PERMISSION_DENIED"),
    (("timeout", "timed out", "deadline"),                  "ERR_TIMEOUT"),
    (("not found", "404", "no such"),                       "ERR_NOT_FOUND"),
    (("network", "connection", "refused", "unreachable"),   "ERR_NETWORK"),
    (("parse", "json", "decode", "serialize"),              "ERR_PARSE"),
    (("memory", "overflow", "oom"),                         "ERR_RESOURCE"),
]

def normalize_error(raw: str | Exception | None) -> str | None:
    """
    Convert a raw error string or exception to a normalized error code.
    This is the ONLY transformation that may occur on errors before they
    influence system state. Raw tracebacks and exception messages are
    never forwarded to the LLM or written to the CognitionInput.
    """
    if raw is None:
        return None
    text = str(raw).lower()
    for patterns, code in _ERROR_PATTERNS:
        if any(p in text for p in patterns):
            return code
    return "ERR_INTERNAL"


# ── LLM-Facing Schemas (the boundary) ────────────────────────────────────────

@dataclass(frozen=True)
class NormalizedState:
    """
    The sanitized view of system state delivered to the LLM.
    Contains NO internal IDs, NO hashes, NO raw error strings.
    All fields are safe to pass to an external inference coprocessor.
    """
    fsm_state:    str                    # "IDLE", "COGNITION_PROPOSING", etc.
    active_goal:  str | None             # Truncated user goal
    pending_steps: int                   # Number of in-flight side effects
    last_outcome: str | None             # "SUCCESS" | "PARTIAL" | "FAILURE" | None
    last_result:  dict[str, Any]         # Normalized observation result
    constraints:  dict[str, Any]         # Explicit constraint annotations


@dataclass(frozen=True)
class CognitionInput:
    """
    The ONLY permissible input to the LLM inference coprocessor.
    Constructed by build_cognition_input() — never constructed ad-hoc.
    """
    normalized_state: NormalizedState
    legal_actions:    frozenset[str]    # ActionType.value strings — never enum objects
    context_window:   str               # Goal text, safely truncated


# ── Boundary Constructor ──────────────────────────────────────────────────────

def build_cognition_input(
    snapshot:         SystemSnapshot,
    capability_graph: dict[ActionType, Capability],
    tier:             str,
    goal:             str,
) -> CognitionInput:
    """
    Pure function. Constructs the normalized CognitionInput from internal state.
    This is the single point of entry into the LLM boundary.

    What is included:
    - FSM state name (string, not enum)
    - Truncated goal (≤ 1000 chars)
    - Count of pending side effects (not their details)
    - Last observation outcome + normalized result
    - Explicit constraints (e.g. authorization_pending)
    - Legal action names for this tier

    What is excluded:
    - sequence_ids, event_hashes, lamport clocks
    - Raw error messages or Python exceptions
    - Side effect parameters or idempotency keys
    - Internal authorization request details
    - Any schema that is not explicitly defined in this module
    """
    legal = get_legal_actions(capability_graph, tier)

    last_outcome: str | None = None
    last_result: dict[str, Any] = {}
    if snapshot.last_observation:
        last_outcome = snapshot.last_observation.outcome
        last_result  = {
            k: v for k, v in snapshot.last_observation.normalized_result.items()
            if isinstance(v, (str, int, float, bool, list, dict)) and not _is_sensitive_key(k)
        }

    constraints: dict[str, Any] = {}
    if snapshot.pending_authorization is not None:
        # Tell the LLM that a request is pending — NOT which request or its ID
        constraints["authorization_pending"] = True
    if snapshot.fsm_state.value in ("HALTED",):
        constraints["terminal"] = True

    normalized = NormalizedState(
        fsm_state=snapshot.fsm_state.value,
        active_goal=goal[:1000] if goal else None,
        pending_steps=sum(
            1 for se in snapshot.active_side_effects
            if se.status == SideEffectStatus.PENDING
        ),
        last_outcome=last_outcome,
        last_result=last_result,
        constraints=constraints,
    )

    return CognitionInput(
        normalized_state=normalized,
        legal_actions=frozenset(a.value for a in legal),
        context_window=goal[:2000] if goal else "",
    )


def _is_sensitive_key(key: str) -> bool:
    """Filter observation result keys that must never cross the LLM boundary."""
    sensitive = {"password", "token", "secret", "key", "hash", "traceback", "stack"}
    return any(s in key.lower() for s in sensitive)


# ── Proposal Validation ───────────────────────────────────────────────────────

class InvalidProposalError(Exception):
    """
    Raised when the LLM output fails schema validation or legal-action checking.
    This exception type is handled by the FSM — it never propagates to the LLM.
    """
    def __init__(self, normalized_reason: str):
        self.normalized_reason = normalized_reason
        super().__init__(normalized_reason)


def validate_proposal(
    raw_output:    dict[str, Any],
    legal_actions: frozenset[str],
) -> ActionProposal:
    """
    Parse and validate the LLM's raw output dict into a typed ActionProposal.

    Two validation layers:
    1. Schema validation (Pydantic): all required fields present and correctly typed.
    2. Authority validation: proposed action_type must be in the legal_actions set.

    On failure, raises InvalidProposalError with a normalized (non-raw) reason.
    The FSM catches this and transitions to COGNITION_PROPOSING for a retry.
    """
    try:
        proposal = ActionProposal(**raw_output)
    except (ValidationError, TypeError) as exc:
        raise InvalidProposalError(
            f"Schema validation failed: {normalize_error(exc)}"
        ) from exc

    if proposal.action_type.value not in legal_actions:
        raise InvalidProposalError(
            f"Proposed action {proposal.action_type.value!r} is not in the legal action set. "
            f"Must be one of: {sorted(legal_actions)}"
        )

    return proposal


# ── Inference Callable Type ───────────────────────────────────────────────────

CognitionCallable = Callable[[CognitionInput], Awaitable[dict[str, Any]]]


async def request_proposal(
    cognition_fn:    CognitionCallable,
    cognition_input: CognitionInput,
) -> ActionProposal:
    """
    Invoke the LLM coprocessor and validate the result.

    The cognition_fn receives only a CognitionInput and must return a raw dict.
    If validation fails, raises InvalidProposalError — the caller (FSM execution
    loop) handles this by logging the normalized failure and retrying or halting.

    The LLM is never informed of the InvalidProposalError itself — on retry, it
    receives a fresh CognitionInput reflecting the current state.
    """
    raw_output = await cognition_fn(cognition_input)
    return validate_proposal(raw_output, cognition_input.legal_actions)
