"""
Pydantic schemas for the W.A.D.E. Immutable Execution Ledger and runtime.

Design invariants:
- Every model that enters the ledger is frozen (model_config = ConfigDict(frozen=True)).
- The LLM never sees any of these schemas directly — CognitionInput is the boundary.
- SystemSnapshot is a derived projection; it is never a source of truth.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class FSMState(str, Enum):
    IDLE                  = "IDLE"
    COGNITION_PROPOSING   = "COGNITION_PROPOSING"
    POLICY_EVALUATION     = "POLICY_EVALUATION"
    PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
    EXECUTING             = "EXECUTING"
    OBSERVATION_ROUTING   = "OBSERVATION_ROUTING"
    HALTED                = "HALTED"


class EventType(str, Enum):
    TASK_CREATED            = "TASK_CREATED"
    FSM_TRANSITIONED        = "FSM_TRANSITIONED"
    COGNITION_PROPOSED      = "COGNITION_PROPOSED"
    POLICY_EVALUATED        = "POLICY_EVALUATED"
    AUTHORIZATION_REQUESTED = "AUTHORIZATION_REQUESTED"
    AUTHORIZATION_RESOLVED  = "AUTHORIZATION_RESOLVED"
    EXECUTION_STARTED       = "EXECUTION_STARTED"
    EXECUTION_COMPLETED     = "EXECUTION_COMPLETED"
    EXECUTION_FAILED        = "EXECUTION_FAILED"
    SIDE_EFFECT_REGISTERED  = "SIDE_EFFECT_REGISTERED"
    SIDE_EFFECT_COMMITTED   = "SIDE_EFFECT_COMMITTED"
    SIDE_EFFECT_ROLLED_BACK = "SIDE_EFFECT_ROLLED_BACK"
    OBSERVATION_RECORDED    = "OBSERVATION_RECORDED"
    COMPENSATION_APPLIED    = "COMPENSATION_APPLIED"
    SYSTEM_HALTED           = "SYSTEM_HALTED"


class PolicyDecisionType(str, Enum):
    APPROVED               = "APPROVED"
    REQUIRES_AUTHORIZATION = "REQUIRES_AUTHORIZATION"
    DENIED                 = "DENIED"


class SideEffectScope(str, Enum):
    INTERNAL = "INTERNAL"   # Local OS — can be compensated
    EXTERNAL = "EXTERNAL"   # Remote API — cannot be perfectly rolled back


class SideEffectStatus(str, Enum):
    PENDING      = "PENDING"
    COMMITTED    = "COMMITTED"
    FAILED       = "FAILED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK  = "ROLLED_BACK"


class AuthorizationResolution(str, Enum):
    APPROVED  = "APPROVED"
    REJECTED  = "REJECTED"
    TIMED_OUT = "TIMED_OUT"


class ActionType(str, Enum):
    READ_FILE             = "READ_FILE"
    WRITE_FILE            = "WRITE_FILE"
    DELETE_FILE           = "DELETE_FILE"
    RUN_SHELL             = "RUN_SHELL"
    WEB_SEARCH            = "WEB_SEARCH"
    HTTP_GET              = "HTTP_GET"
    HTTP_POST             = "HTTP_POST"
    STORE_MEMORY          = "STORE_MEMORY"
    RETRIEVE_MEMORY       = "RETRIEVE_MEMORY"
    COMPLETE_TASK         = "COMPLETE_TASK"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"


# ── Immutable Ledger Event ────────────────────────────────────────────────────

class LedgerEvent(BaseModel):
    """
    The atomic unit of the Immutable Execution Ledger.
    Every field is required for hash-chain verification and causal ordering.
    Frozen — once created, it cannot be mutated.
    """
    model_config = ConfigDict(frozen=True)

    sequence_id:   int            # Monotonic, gapless
    event_time:    datetime       # Event-source timestamp (stored in ledger, used for TTL)
    event_type:    EventType
    payload:       dict[str, Any]
    lamport_clock: int            # Logical clock for causal ordering
    prev_hash:     str            # SHA-256 hex of predecessor (64 zeros for genesis)
    event_hash:    str            # SHA-256 hex of canonical representation of this event

    @field_validator("prev_hash", "event_hash")
    @classmethod
    def _valid_sha256(cls, v: str) -> str:
        if len(v) != 64 or not all(c in "0123456789abcdef" for c in v):
            raise ValueError(f"Expected 64-char hex SHA-256, got: {v!r}")
        return v


# ── Typed Event Payloads ──────────────────────────────────────────────────────

class TaskCreatedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:    str
    goal:       str
    tier:       str
    session_id: str
    created_by: str


class FSMTransitionedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:    str
    from_state: FSMState
    to_state:   FSMState
    reason:     str


class ActionProposal(BaseModel):
    """
    The ONLY valid output from the LLM cognitive layer.
    Strictly typed — no free-form strings in parameters.
    """
    model_config = ConfigDict(frozen=True)

    proposal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_type: ActionType
    parameters:  dict[str, str | int | float | bool]
    rationale:   str = Field(max_length=500)
    confidence:  float = Field(ge=0.0, le=1.0)


class CognitionProposedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:      str
    proposal:     ActionProposal
    input_tokens: int   # Auditable inference cost
    model_id:     str


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    decision:           PolicyDecisionType
    policy_version:     str           # Exact version — required for replay determinism
    capability_matched: str | None
    denial_reason:      str | None
    authz_ttl_seconds:  int | None    # TTL for authorization window (event-time based)


class PolicyEvaluatedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:  str
    proposal: ActionProposal
    decision: PolicyDecision


class AuthorizationRequestedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id:       str
    task_id:          str
    proposal:         ActionProposal
    requested_at_seq: int   # Sequence ID of THIS event — TTL derived from its event_time
    ttl_seconds:      int


class AuthorizationResolvedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id:     str
    task_id:        str
    resolution:     AuthorizationResolution
    resolved_by:    str   # User ID or "timeout_enforcer"
    resolver_clock: int   # Lamport clock of the resolver at resolution time


class SideEffectRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    idempotency_key:              str
    action_type:                  ActionType
    scope:                        SideEffectScope
    parameters:                   dict[str, str | int | float | bool]
    retry_limit:                  int = Field(default=3, ge=0, le=10)
    retry_count:                  int = Field(default=0, ge=0)
    backoff_factor:               float = Field(default=2.0, ge=1.0)
    status:                       SideEffectStatus = SideEffectStatus.PENDING
    result:                       dict[str, Any] | None = None
    error:                        str | None = None   # Normalized error code only
    external_reconciliation_id:   str | None = None   # For EXTERNAL scope tracking


class SideEffectRegisteredPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:     str
    side_effect: SideEffectRecord


class SideEffectCommittedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:         str
    idempotency_key: str
    result:          dict[str, Any]


class SideEffectRolledBackPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:         str
    idempotency_key: str
    reason:          str


class ObservationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    observation_id:    str
    action_type:       ActionType
    outcome:           Literal["SUCCESS", "PARTIAL", "FAILURE"]
    normalized_result: dict[str, Any]   # Never raw output — always normalized


class ObservationRecordedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:     str
    observation: ObservationRecord


class CompensationAppliedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:         str
    idempotency_key: str
    compensation_fn: str   # Name of compensation function — for auditability
    result:          str   # "OK" or normalized error code


class SystemHaltedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id:   str | None
    reason:    str
    halt_code: str


# ── System State Projection ───────────────────────────────────────────────────

class SystemSnapshot(BaseModel):
    """
    A cached projection of ledger state.
    NEVER used as a source of truth — always reconstructed by reduce().
    Frozen to prevent accidental mutation outside the reducer.
    """
    model_config = ConfigDict(frozen=True)

    task_id:               str | None = None
    fsm_state:             FSMState = FSMState.IDLE
    current_proposal:      ActionProposal | None = None
    active_side_effects:   tuple[SideEffectRecord, ...] = ()
    pending_authorization: AuthorizationRequestedPayload | None = None
    last_observation:      ObservationRecord | None = None
    ledger_tip_hash:       str = "0" * 64
    sequence_id:           int = 0
    lamport_clock:         int = 0
    event_count:           int = 0
