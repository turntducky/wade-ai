"""
W.A.D.E. Core Runtime — Public API

Six-phase architecture:
    Phase 1 — Immutable Execution Ledger:   Ledger, reduce, LedgerIntegrityError
    Phase 2 — FSM Execution Kernel:         ExecutionKernel, InvalidTransitionError, InvariantViolationError
    Phase 3 — Pure Policy Engine:           evaluate_policy, PolicyContext, CURRENT_POLICY_VERSION
    Phase 4 — Idempotency & Reconciliation: execute_with_idempotency, reconcile_external
    Phase 5 — Cognitive Layer Isolation:    build_cognition_input, request_proposal, CognitionInput
    Phase 6 — Exactly-Once Transport:       LamportClock, AuthorizationResolver, TransportMessage
"""
from app.core.runtime.schemas import (
    FSMState,
    EventType,
    LedgerEvent,
    ActionProposal,
    ActionType,
    PolicyDecision,
    PolicyDecisionType,
    SideEffectRecord,
    SideEffectScope,
    SideEffectStatus,
    AuthorizationResolution,
    SystemSnapshot,
    TaskCreatedPayload,
    FSMTransitionedPayload,
    CognitionProposedPayload,
    PolicyEvaluatedPayload,
    AuthorizationRequestedPayload,
    AuthorizationResolvedPayload,
    SideEffectRegisteredPayload,
    SideEffectCommittedPayload,
    SideEffectRolledBackPayload,
    ObservationRecord,
    ObservationRecordedPayload,
    CompensationAppliedPayload,
    SystemHaltedPayload,
)
from app.core.runtime.ledger import (
    Ledger,
    reduce,
    LedgerIntegrityError,
)
from app.core.runtime.fsm import (
    ExecutionKernel,
    InvalidTransitionError,
    InvariantViolationError,
)
from app.core.runtime.policy import (
    evaluate_policy,
    get_legal_actions,
    get_versioned_capability_graph,
    PolicyContext,
    Capability,
    CapabilityRisk,
    CURRENT_POLICY_VERSION,
)
from app.core.runtime.cognition import (
    build_cognition_input,
    validate_proposal,
    request_proposal,
    CognitionInput,
    NormalizedState,
    InvalidProposalError,
    normalize_error,
)
from app.core.runtime.idempotency import (
    execute_with_idempotency,
    reconcile_external,
    extract_committed_keys,
    SideEffectError,
    AlreadyCommittedError,
    ExecutionReceipt,
    ExternalReconciliationRecord,
)
from app.core.runtime.transport import (
    LamportClock,
    TransportMessage,
    AuthorizationResolver,
)

__all__ = [
    # ── Phase 1: Ledger & Reducer ──────────────────────────────────────────
    "Ledger", "reduce", "LedgerIntegrityError",
    "LedgerEvent",
    # ── Phase 1: Event Payloads ────────────────────────────────────────────
    "TaskCreatedPayload", "FSMTransitionedPayload", "CognitionProposedPayload",
    "PolicyEvaluatedPayload", "AuthorizationRequestedPayload",
    "AuthorizationResolvedPayload", "SideEffectRegisteredPayload",
    "SideEffectCommittedPayload", "SideEffectRolledBackPayload",
    "ObservationRecordedPayload", "CompensationAppliedPayload", "SystemHaltedPayload",
    # ── Phase 1: Enumerations ──────────────────────────────────────────────
    "FSMState", "EventType", "ActionType", "PolicyDecisionType",
    "SideEffectScope", "SideEffectStatus", "AuthorizationResolution",
    # ── Phase 1: Core Schemas ──────────────────────────────────────────────
    "ActionProposal", "PolicyDecision", "SideEffectRecord",
    "ObservationRecord", "SystemSnapshot",
    # ── Phase 2: FSM Kernel ────────────────────────────────────────────────
    "ExecutionKernel", "InvalidTransitionError", "InvariantViolationError",
    # ── Phase 3: Policy Engine ─────────────────────────────────────────────
    "evaluate_policy", "get_legal_actions", "get_versioned_capability_graph",
    "PolicyContext", "Capability", "CapabilityRisk", "CURRENT_POLICY_VERSION",
    # ── Phase 4: Idempotency & Reconciliation ──────────────────────────────
    "execute_with_idempotency", "reconcile_external", "extract_committed_keys",
    "SideEffectError", "AlreadyCommittedError", "ExecutionReceipt",
    "ExternalReconciliationRecord",
    # ── Phase 5: Cognitive Layer ───────────────────────────────────────────
    "build_cognition_input", "validate_proposal", "request_proposal",
    "CognitionInput", "NormalizedState", "InvalidProposalError", "normalize_error",
    # ── Phase 6: Transport ─────────────────────────────────────────────────
    "LamportClock", "TransportMessage", "AuthorizationResolver",
]
