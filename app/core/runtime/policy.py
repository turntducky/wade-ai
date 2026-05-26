"""
Pure Policy Engine — Phase 3 of the architectural specification.

Constraints (enforced by design — not just convention):
- evaluate_policy() is a pure function: f(PolicyContext) → PolicyDecision.
- No side effects. No external calls. No mutable state. No wall-clock queries.
- Policy versioning is mandatory: every PolicyDecision records the exact
  policy_version used, guaranteeing that replaying the ledger produces the same
  authorization decisions even after the capability graph evolves.
- TTL for PENDING_AUTHORIZATION windows is recorded in the event (authz_ttl_seconds)
  and consumed by the transport layer using event-time, not wall-clock time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.runtime.schemas import (
    ActionProposal, ActionType, PolicyDecision, PolicyDecisionType,
    SystemSnapshot, FSMState,
)

CURRENT_POLICY_VERSION = "1.0.0"


# ── Capability Graph ──────────────────────────────────────────────────────────

class CapabilityRisk(str, Enum):
    LOW      = "LOW"       # Approved directly; no authorization needed
    MEDIUM   = "MEDIUM"    # Requires human authorization
    HIGH     = "HIGH"      # Always requires human authorization; shorter TTL
    CRITICAL = "CRITICAL"  # Permanently denied; cannot be authorized


@dataclass(frozen=True)
class Capability:
    action_type:       ActionType
    risk_level:        CapabilityRisk
    allowed_tiers:     frozenset[str]
    authz_ttl_seconds: int = 300
    constraints:       dict[str, Any] = field(default_factory=dict)


# ── Policy Ruleset v1.0.0 ─────────────────────────────────────────────────────

_CAPABILITY_GRAPH_V1: dict[ActionType, Capability] = {
    ActionType.READ_FILE: Capability(
        action_type=ActionType.READ_FILE,
        risk_level=CapabilityRisk.LOW,
        allowed_tiers=frozenset({"admin", "family"}),
    ),
    ActionType.WRITE_FILE: Capability(
        action_type=ActionType.WRITE_FILE,
        risk_level=CapabilityRisk.MEDIUM,
        allowed_tiers=frozenset({"admin"}),
        authz_ttl_seconds=120,
    ),
    ActionType.DELETE_FILE: Capability(
        action_type=ActionType.DELETE_FILE,
        risk_level=CapabilityRisk.HIGH,
        allowed_tiers=frozenset({"admin"}),
        authz_ttl_seconds=60,
    ),
    ActionType.RUN_SHELL: Capability(
        action_type=ActionType.RUN_SHELL,
        risk_level=CapabilityRisk.HIGH,
        allowed_tiers=frozenset({"admin"}),
        authz_ttl_seconds=60,
    ),
    ActionType.WEB_SEARCH: Capability(
        action_type=ActionType.WEB_SEARCH,
        risk_level=CapabilityRisk.LOW,
        allowed_tiers=frozenset({"admin", "family", "friends", "guests"}),
    ),
    ActionType.HTTP_GET: Capability(
        action_type=ActionType.HTTP_GET,
        risk_level=CapabilityRisk.MEDIUM,
        allowed_tiers=frozenset({"admin", "family"}),
        authz_ttl_seconds=180,
    ),
    ActionType.HTTP_POST: Capability(
        action_type=ActionType.HTTP_POST,
        risk_level=CapabilityRisk.HIGH,
        allowed_tiers=frozenset({"admin"}),
        authz_ttl_seconds=60,
    ),
    ActionType.STORE_MEMORY: Capability(
        action_type=ActionType.STORE_MEMORY,
        risk_level=CapabilityRisk.LOW,
        allowed_tiers=frozenset({"admin", "family", "friends", "guests"}),
    ),
    ActionType.RETRIEVE_MEMORY: Capability(
        action_type=ActionType.RETRIEVE_MEMORY,
        risk_level=CapabilityRisk.LOW,
        allowed_tiers=frozenset({"admin", "family", "friends", "guests"}),
    ),
    ActionType.COMPLETE_TASK: Capability(
        action_type=ActionType.COMPLETE_TASK,
        risk_level=CapabilityRisk.LOW,
        allowed_tiers=frozenset({"admin", "family", "friends", "guests", "strangers"}),
    ),
    ActionType.REQUEST_CLARIFICATION: Capability(
        action_type=ActionType.REQUEST_CLARIFICATION,
        risk_level=CapabilityRisk.LOW,
        allowed_tiers=frozenset({"admin", "family", "friends", "guests", "strangers"}),
    ),
}

# Version registry — add new rulesets here as the capability graph evolves.
# Old versions remain registered so the ledger can replay historical decisions correctly.
_VERSIONED_GRAPHS: dict[str, dict[ActionType, Capability]] = {
    "1.0.0": _CAPABILITY_GRAPH_V1,
}


# ── Policy Context (immutable input to the pure function) ─────────────────────

@dataclass(frozen=True)
class PolicyContext:
    """
    All inputs required by evaluate_policy(). Frozen — no mutation allowed.
    policy_version is explicit: the caller names the ruleset, not the engine.
    """
    snapshot:         SystemSnapshot
    capability_graph: dict[ActionType, Capability]
    proposed_action:  ActionProposal
    policy_version:   str
    tier:             str


# ── Pure Evaluation Function ──────────────────────────────────────────────────

def evaluate_policy(ctx: PolicyContext) -> PolicyDecision:
    """
    Pure policy evaluation.

    Evaluation order (first rule that matches wins):
      1. Unknown action type        → DENIED
      2. Tier not in allowed_tiers  → DENIED
      3. CRITICAL risk              → DENIED
      4. FSM in invalid state       → DENIED
      5. HIGH or MEDIUM risk        → REQUIRES_AUTHORIZATION
      6. LOW risk                   → APPROVED

    Returns a PolicyDecision that includes the exact policy_version used.
    This version string must be written to the ledger so future replays
    can reconstruct the same decision with the same ruleset.
    """
    action_type = ctx.proposed_action.action_type
    capability  = ctx.capability_graph.get(action_type)

    # Rule 1: Unknown action → deny
    if capability is None:
        return PolicyDecision(
            decision=PolicyDecisionType.DENIED,
            policy_version=ctx.policy_version,
            capability_matched=None,
            denial_reason=f"No capability registered for {action_type.value!r}",
            authz_ttl_seconds=None,
        )

    # Rule 2: Tier not permitted → deny
    if ctx.tier not in capability.allowed_tiers:
        return PolicyDecision(
            decision=PolicyDecisionType.DENIED,
            policy_version=ctx.policy_version,
            capability_matched=action_type.value,
            denial_reason=(
                f"Tier {ctx.tier!r} may not invoke {action_type.value}. "
                f"Permitted tiers: {sorted(capability.allowed_tiers)}"
            ),
            authz_ttl_seconds=None,
        )

    # Rule 3: Permanently denied capability
    if capability.risk_level == CapabilityRisk.CRITICAL:
        return PolicyDecision(
            decision=PolicyDecisionType.DENIED,
            policy_version=ctx.policy_version,
            capability_matched=action_type.value,
            denial_reason=f"{action_type.value} is permanently denied (CRITICAL risk level).",
            authz_ttl_seconds=None,
        )

    # Rule 4: FSM must be in an evaluation-eligible state
    if ctx.snapshot.fsm_state not in (FSMState.COGNITION_PROPOSING, FSMState.POLICY_EVALUATION):
        return PolicyDecision(
            decision=PolicyDecisionType.DENIED,
            policy_version=ctx.policy_version,
            capability_matched=action_type.value,
            denial_reason=(
                f"Policy evaluation is only legal in COGNITION_PROPOSING or POLICY_EVALUATION. "
                f"Current state: {ctx.snapshot.fsm_state.value}"
            ),
            authz_ttl_seconds=None,
        )

    # Rule 5: HIGH or MEDIUM risk → requires authorization
    if capability.risk_level in (CapabilityRisk.HIGH, CapabilityRisk.MEDIUM):
        return PolicyDecision(
            decision=PolicyDecisionType.REQUIRES_AUTHORIZATION,
            policy_version=ctx.policy_version,
            capability_matched=action_type.value,
            denial_reason=None,
            authz_ttl_seconds=capability.authz_ttl_seconds,
        )

    # Rule 6: LOW risk → approved
    return PolicyDecision(
        decision=PolicyDecisionType.APPROVED,
        policy_version=ctx.policy_version,
        capability_matched=action_type.value,
        denial_reason=None,
        authz_ttl_seconds=None,
    )


# ── Supporting Pure Functions ─────────────────────────────────────────────────

def get_legal_actions(
    capability_graph: dict[ActionType, Capability],
    tier: str,
) -> frozenset[ActionType]:
    """
    Pure function. Returns the set of actions that are non-CRITICAL and
    tier-permitted. Used to build the CognitionInput — the LLM only sees
    actions in this set as candidates.
    """
    return frozenset(
        at for at, cap in capability_graph.items()
        if tier in cap.allowed_tiers and cap.risk_level != CapabilityRisk.CRITICAL
    )


def get_versioned_capability_graph(version: str) -> dict[ActionType, Capability]:
    """Return the capability graph for an exact policy version string."""
    graph = _VERSIONED_GRAPHS.get(version)
    if graph is None:
        raise ValueError(f"No capability graph registered for policy version {version!r}")
    return graph
