"""
Idempotency & Side-Effect Reconciliation — Phase 4 of the architectural specification.

Guarantees:
- Every side-effect execution is gated on an idempotency_key that is checked
  against the committed ledger state before execution begins.
- On failure: exponential backoff up to retry_limit, then compensation.
- On compensation failure: SideEffectError is raised with a normalized error code.
  The FSM transitions to HALTED; no raw exception escapes to the LLM.
- External side effects (EXTERNAL scope) receive a reconciliation record that
  tracks expected vs. observed state to detect drift. They cannot be rolled back —
  they can only be reconciled.

Note on rollback semantics:
- INTERNAL: "rolled back" means the compensation function was invoked and the
  local filesystem/network state was restored to its pre-execution snapshot.
- EXTERNAL: "rolled back" is a ledger fiction — we can only record that we
  attempted compensation. The external system may or may not have processed it.
  ExternalReconciliationRecord tracks actual observed state for alerting.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.runtime.schemas import (
    ActionType, SideEffectRecord, SideEffectScope, SideEffectStatus,
)
from app.core.runtime.cognition import normalize_error

logger = logging.getLogger("wade.runtime.idempotency")


# ── Callables ─────────────────────────────────────────────────────────────────

SideEffectFn  = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
CompensateFn  = Callable[[dict[str, Any]], Awaitable[None]]
ObserveFn     = Callable[[str], Awaitable[dict[str, Any]]]   # For external reconciliation


# ── Result Types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionReceipt:
    idempotency_key: str
    result:          dict[str, Any]
    attempt_count:   int
    committed_at:    datetime


@dataclass
class ExternalReconciliationRecord:
    """
    Tracks state drift between the local FSM and an external system.
    Mutable — updated by reconcile_external() on each polling cycle.
    """
    reconciliation_id: str
    idempotency_key:   str
    action_type:       ActionType
    submitted_at:      datetime
    expected_state:    dict[str, Any]
    observed_state:    dict[str, Any] = field(default_factory=dict)
    drift_detected:    bool = False
    last_checked_at:   datetime | None = None


# ── Exception Types ───────────────────────────────────────────────────────────

class SideEffectError(Exception):
    """
    Raised after retry_limit is exhausted and compensation fails.
    Contains only a normalized error code — never a raw traceback.
    """
    def __init__(self, idempotency_key: str, normalized_error: str) -> None:
        self.idempotency_key  = idempotency_key
        self.normalized_error = normalized_error
        super().__init__(f"Side effect {idempotency_key!r} failed: {normalized_error}")


class AlreadyCommittedError(Exception):
    """
    Raised when a side effect with this idempotency_key is already committed
    in the ledger. The caller must treat this as a no-op, not a failure.
    """
    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(f"Idempotency key {idempotency_key!r} already committed")


# ── Core Execution ────────────────────────────────────────────────────────────

async def execute_with_idempotency(
    side_effect:    SideEffectRecord,
    execute_fn:     SideEffectFn,
    compensate_fn:  CompensateFn,
    committed_keys: frozenset[str],   # Immutable view from ledger — never a live dict
) -> ExecutionReceipt:
    """
    Execute a side effect with full idempotency, retry, and compensation guarantees.

    Retry schedule (backoff_factor=2.0, retry_limit=3):
        attempt 0: immediate
        attempt 1: 1.0s delay
        attempt 2: 3.0s delay
        attempt 3: 7.0s delay  ← last attempt

    On exhaustion: compensation is attempted. If that also fails, SideEffectError
    is raised with a normalized code. The FSM transitions to HALTED.

    The committed_keys parameter is derived from the ledger at call time and is
    immutable — this prevents a TOCTOU race on the idempotency check.
    """
    key = side_effect.idempotency_key

    if key in committed_keys:
        raise AlreadyCommittedError(key)

    last_error = "ERR_UNKNOWN"
    for attempt in range(side_effect.retry_limit + 1):
        if attempt > 0:
            delay = (side_effect.backoff_factor ** attempt) - 1.0
            logger.info(
                "[IDEMPOTENCY] Backoff %.1fs — key=%r attempt=%d/%d",
                delay, key, attempt, side_effect.retry_limit,
            )
            await asyncio.sleep(delay)

        try:
            result = await execute_fn(dict(side_effect.parameters))
            return ExecutionReceipt(
                idempotency_key=key,
                result=result,
                attempt_count=attempt + 1,
                committed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            last_error = normalize_error(exc) or "ERR_INTERNAL"
            logger.warning(
                "[IDEMPOTENCY] Attempt %d failed — key=%r error=%s",
                attempt + 1, key, last_error,
            )

    # Retry limit exhausted → compensate
    logger.error(
        "[IDEMPOTENCY] Retry limit exhausted — key=%r. Invoking compensation.",
        key,
    )
    try:
        await compensate_fn(dict(side_effect.parameters))
        logger.info("[IDEMPOTENCY] Compensation succeeded — key=%r", key)
    except Exception as comp_exc:
        comp_error = normalize_error(comp_exc) or "ERR_INTERNAL"
        logger.critical(
            "[IDEMPOTENCY] Compensation FAILED — key=%r error=%s. "
            "Manual intervention required.",
            key, comp_error,
        )
        raise SideEffectError(key, f"ERR_COMPENSATION_FAILED:{comp_error}") from comp_exc

    raise SideEffectError(key, last_error)


# ── External Reconciliation ───────────────────────────────────────────────────

async def reconcile_external(
    record:     ExternalReconciliationRecord,
    observe_fn: ObserveFn,
) -> ExternalReconciliationRecord:
    """
    Poll the external system and update the reconciliation record to reflect drift.

    Returns a NEW record (does not mutate the input) — callers persist this
    by writing a COMPENSATION_APPLIED or alerting event to the ledger.

    Drift is defined as: observed_state != expected_state.
    What action to take on drift is a policy decision made by the caller.
    """
    try:
        observed = await observe_fn(record.reconciliation_id)
    except Exception as exc:
        logger.warning(
            "[RECONCILE] Could not observe external state for id=%r: %s",
            record.reconciliation_id, normalize_error(exc),
        )
        observed = {}

    drift = observed != record.expected_state
    if drift and not record.drift_detected:
        logger.warning(
            "[RECONCILE] State drift detected for id=%r — expected=%r observed=%r",
            record.reconciliation_id, record.expected_state, observed,
        )

    return ExternalReconciliationRecord(
        reconciliation_id=record.reconciliation_id,
        idempotency_key=record.idempotency_key,
        action_type=record.action_type,
        submitted_at=record.submitted_at,
        expected_state=record.expected_state,
        observed_state=observed,
        drift_detected=drift,
        last_checked_at=datetime.now(timezone.utc),
    )


# ── Committed Key Extractor ───────────────────────────────────────────────────

def extract_committed_keys(events: list) -> frozenset[str]:
    """
    Pure function. Extract committed idempotency keys from the ledger event log.
    Returns a frozenset — immutable, safe to pass to execute_with_idempotency().
    Import EventType locally to avoid circular imports from schemas.
    """
    from app.core.runtime.schemas import EventType
    return frozenset(
        e.payload.get("idempotency_key", "")
        for e in events
        if e.event_type == EventType.SIDE_EFFECT_COMMITTED
        and e.payload.get("idempotency_key")
    )
