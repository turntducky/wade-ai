"""
FSM Execution Kernel — Phase 2 of the architectural specification.

Authority model:
- Only ExecutionKernel.validate_transition() may gate a state change.
- The LLM has zero authority here. Its proposal enters via the cognition layer
  and exits as an ActionProposal; the FSM decides what happens next.
- Invariants are enforced at two levels:
    Structural (in-memory): checked against the post-transition snapshot.
    Ledger-dependent: checked against the full event log before a transition is allowed.

The kernel does NOT write to the ledger — that is the responsibility of the
caller (the execution loop). validate_transition() and validate_post_transition()
are pure checks that raise on violation.
"""
from __future__ import annotations

from typing import Callable

from app.core.runtime.schemas import (
    FSMState, LedgerEvent, EventType, SystemSnapshot, SideEffectStatus,
)


# ── Transition Table ──────────────────────────────────────────────────────────

_VALID_TRANSITIONS: dict[FSMState, frozenset[FSMState]] = {
    FSMState.IDLE: frozenset({
        FSMState.COGNITION_PROPOSING,
        FSMState.HALTED,
    }),
    FSMState.COGNITION_PROPOSING: frozenset({
        FSMState.POLICY_EVALUATION,
        FSMState.HALTED,
    }),
    FSMState.POLICY_EVALUATION: frozenset({
        FSMState.EXECUTING,             # Policy approved
        FSMState.PENDING_AUTHORIZATION, # Policy requires HITL
        FSMState.COGNITION_PROPOSING,   # Policy denied → re-propose
        FSMState.HALTED,
    }),
    FSMState.PENDING_AUTHORIZATION: frozenset({
        FSMState.EXECUTING,             # Human approved
        FSMState.COGNITION_PROPOSING,   # Human rejected → re-propose
        FSMState.HALTED,                # Timeout or emergency
    }),
    FSMState.EXECUTING: frozenset({
        FSMState.OBSERVATION_ROUTING,
        FSMState.HALTED,
    }),
    FSMState.OBSERVATION_ROUTING: frozenset({
        FSMState.COGNITION_PROPOSING,   # More steps needed
        FSMState.IDLE,                  # Task complete
        FSMState.HALTED,
    }),
    FSMState.HALTED: frozenset(),       # Terminal — no exits
}


# ── Exception Types ───────────────────────────────────────────────────────────

class InvalidTransitionError(Exception):
    """The requested FSM transition is not permitted by the transition table."""


class InvariantViolationError(Exception):
    """A structural or ledger invariant has been violated."""


# ── Invariant Definitions ─────────────────────────────────────────────────────

StructuralInvariant = Callable[[SystemSnapshot], None]
LedgerInvariant     = Callable[[SystemSnapshot, list[LedgerEvent]], None]


def _inv_single_active_execution(snapshot: SystemSnapshot) -> None:
    """
    Structural: While in EXECUTING state, at most one side effect may be PENDING.
    Prevents race conditions from concurrent tool dispatches.
    """
    if snapshot.fsm_state == FSMState.EXECUTING:
        pending = [se for se in snapshot.active_side_effects
                   if se.status == SideEffectStatus.PENDING]
        if len(pending) > 1:
            raise InvariantViolationError(
                f"Multiple active executions in EXECUTING state: "
                f"{[se.idempotency_key for se in pending]}"
            )


def _inv_no_proposal_when_idle_or_halted(snapshot: SystemSnapshot) -> None:
    """
    Structural: No unconsumed ActionProposal may exist in IDLE or HALTED states.
    A proposal must be evaluated or cleared before the FSM comes to rest.
    """
    if snapshot.fsm_state in (FSMState.IDLE, FSMState.HALTED):
        if snapshot.current_proposal is not None:
            raise InvariantViolationError(
                f"Unconsumed proposal {snapshot.current_proposal.proposal_id!r} "
                f"in {snapshot.fsm_state.value} state"
            )


def _inv_authorization_only_in_pending(snapshot: SystemSnapshot) -> None:
    """
    Structural: A pending_authorization record may only exist in PENDING_AUTHORIZATION state.
    """
    if snapshot.pending_authorization is not None:
        if snapshot.fsm_state != FSMState.PENDING_AUTHORIZATION:
            raise InvariantViolationError(
                f"pending_authorization present but FSM is in {snapshot.fsm_state.value}"
            )


def _ledger_inv_no_duplicate_committed_keys(
    snapshot: SystemSnapshot, events: list[LedgerEvent]
) -> None:
    """
    Ledger-dependent: No two SIDE_EFFECT_COMMITTED events may share an idempotency key.
    Detects replay or double-execution bugs across the full causal history.
    """
    committed: set[str] = set()
    for event in events:
        if event.event_type == EventType.SIDE_EFFECT_COMMITTED:
            key = event.payload.get("idempotency_key", "")
            if key in committed:
                raise InvariantViolationError(
                    f"Idempotency violation: key {key!r} committed more than once in ledger"
                )
            committed.add(key)


def _ledger_inv_no_duplicate_authorization_resolution(
    snapshot: SystemSnapshot, events: list[LedgerEvent]
) -> None:
    """
    Ledger-dependent: Each authorization request must be resolved exactly once.
    The transport layer enforces this at write time; this invariant detects ledger corruption.
    """
    resolved: set[str] = set()
    for event in events:
        if event.event_type == EventType.AUTHORIZATION_RESOLVED:
            rid = event.payload.get("request_id", "")
            if rid in resolved:
                raise InvariantViolationError(
                    f"Authorization request {rid!r} resolved more than once in ledger"
                )
            resolved.add(rid)


_STRUCTURAL_INVARIANTS: list[StructuralInvariant] = [
    _inv_single_active_execution,
    _inv_no_proposal_when_idle_or_halted,
    _inv_authorization_only_in_pending,
]

_LEDGER_INVARIANTS: list[LedgerInvariant] = [
    _ledger_inv_no_duplicate_committed_keys,
    _ledger_inv_no_duplicate_authorization_resolution,
]


# ── Execution Kernel ──────────────────────────────────────────────────────────

class ExecutionKernel:
    """
    The sole authority over FSM state transitions.

    Usage pattern (in the execution loop):
        kernel.validate_transition(snapshot, to_state, events)   # raises if invalid
        event = ledger.append(FSM_TRANSITIONED, ...)             # write to ledger
        new_snapshot = reduce(ledger.read_all())                 # derive new state
        kernel.validate_post_transition(new_snapshot)            # check structural invariants
    """

    def validate_transition(
        self,
        snapshot:  SystemSnapshot,
        to_state:  FSMState,
        events:    list[LedgerEvent],
    ) -> None:
        """
        Gate a proposed transition. Does NOT apply it.

        1. Rejects any transition out of HALTED (terminal).
        2. Validates against the transition table.
        3. Runs all ledger-dependent invariants against the full event log.
        """
        from_state = snapshot.fsm_state

        if from_state == FSMState.HALTED:
            raise InvalidTransitionError(
                "HALTED is a terminal state. No transitions are permitted."
            )

        permitted = _VALID_TRANSITIONS.get(from_state, frozenset())
        if to_state not in permitted:
            raise InvalidTransitionError(
                f"{from_state.value} → {to_state.value} is not a valid transition. "
                f"Permitted targets: {sorted(s.value for s in permitted)}"
            )

        for inv in _LEDGER_INVARIANTS:
            inv(snapshot, events)

    def validate_post_transition(self, snapshot: SystemSnapshot) -> None:
        """
        Enforce structural invariants on the snapshot AFTER a transition has been
        committed to the ledger and reduce() has been called to produce the new snapshot.
        """
        for inv in _STRUCTURAL_INVARIANTS:
            inv(snapshot)

    def legal_transitions(self, snapshot: SystemSnapshot) -> frozenset[FSMState]:
        """Return the set of states reachable from the current snapshot state."""
        if snapshot.fsm_state == FSMState.HALTED:
            return frozenset()
        return _VALID_TRANSITIONS.get(snapshot.fsm_state, frozenset())
