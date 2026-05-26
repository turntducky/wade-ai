"""
Immutable Execution Ledger — Phase 1 of the architectural specification.

Guarantees:
- Append-only: no UPDATE or DELETE ever touches the ledger table.
- Cryptographic lineage: every event carries a SHA-256 hash of its predecessor.
- Durable: WAL + FULL sync — committed events survive process crashes.
- Thread-safe: reentrant lock guards all ledger operations.

The canonical reduce() function at the bottom of this module is the ONLY
legitimate way to derive current system state. Any code that bypasses it
and reads state from a mutable in-process object is an architectural violation.
"""
from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.runtime.schemas import (
    LedgerEvent, EventType, SystemSnapshot, FSMState,
    ActionProposal, SideEffectRecord, SideEffectStatus,
    AuthorizationRequestedPayload, ObservationRecord,
    TaskCreatedPayload, FSMTransitionedPayload,
    CognitionProposedPayload, PolicyEvaluatedPayload, PolicyDecisionType,
    AuthorizationResolvedPayload, SideEffectRegisteredPayload,
    SideEffectCommittedPayload, SideEffectRolledBackPayload,
    ObservationRecordedPayload, SystemHaltedPayload,
)

_GENESIS_PREV_HASH = "0" * 64


# ── Canonical Serialisation ───────────────────────────────────────────────────

def _canonical_bytes(
    sequence_id: int,
    event_time: datetime,
    event_type: EventType,
    payload: dict[str, Any],
    prev_hash: str,
) -> bytes:
    """
    Deterministic canonical form for hashing.
    Sorted keys, no whitespace, ASCII-safe — identical output on every platform.
    """
    doc = {
        "sequence_id": sequence_id,
        "event_time":  event_time.isoformat(),
        "event_type":  event_type.value,
        "payload":     payload,
        "prev_hash":   prev_hash,
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _compute_hash(
    sequence_id: int,
    event_time: datetime,
    event_type: EventType,
    payload: dict[str, Any],
    prev_hash: str,
) -> str:
    return hashlib.sha256(
        _canonical_bytes(sequence_id, event_time, event_type, payload, prev_hash)
    ).hexdigest()


# ── Ledger ────────────────────────────────────────────────────────────────────

class LedgerIntegrityError(Exception):
    """Raised when the hash chain is broken or a stored hash doesn't match its recomputed value."""


class Ledger:
    """
    Append-only, cryptographically chained, SQLite-backed event store.

    The ledger is the single source of truth for the entire W.A.D.E. runtime.
    All other state (FSM, snapshots, active tasks) is derived from it via reduce().
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock    = threading.RLock()
        self._conn    = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")   # Durability over speed
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS ledger (
                    sequence_id    INTEGER PRIMARY KEY,
                    event_time     TEXT    NOT NULL,
                    event_type     TEXT    NOT NULL,
                    payload        TEXT    NOT NULL,
                    lamport_clock  INTEGER NOT NULL,
                    prev_hash      TEXT    NOT NULL CHECK(length(prev_hash)  = 64),
                    event_hash     TEXT    NOT NULL CHECK(length(event_hash) = 64)
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_type ON ledger(event_type)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sequence   ON ledger(sequence_id)"
            )

    # ── Write path ────────────────────────────────────────────────────────────

    def append(
        self,
        event_type:    EventType,
        payload:       dict[str, Any],
        lamport_clock: int,
        event_time:    datetime | None = None,
    ) -> LedgerEvent:
        """
        Append one event to the ledger and return the committed LedgerEvent.
        Computes sequence_id and hashes atomically under the lock.
        """
        with self._lock:
            if event_time is None:
                event_time = datetime.now(timezone.utc)

            tip         = self._tip()
            prev_hash   = tip.event_hash if tip else _GENESIS_PREV_HASH
            sequence_id = (tip.sequence_id + 1) if tip else 0
            event_hash  = _compute_hash(sequence_id, event_time, event_type, payload, prev_hash)
            payload_json = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )

            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO ledger
                        (sequence_id, event_time, event_type, payload, lamport_clock, prev_hash, event_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sequence_id,
                        event_time.isoformat(),
                        event_type.value,
                        payload_json,
                        lamport_clock,
                        prev_hash,
                        event_hash,
                    ),
                )

            return LedgerEvent(
                sequence_id=sequence_id,
                event_time=event_time,
                event_type=event_type,
                payload=payload,
                lamport_clock=lamport_clock,
                prev_hash=prev_hash,
                event_hash=event_hash,
            )

    # ── Read paths ────────────────────────────────────────────────────────────

    def read_all(self) -> list[LedgerEvent]:
        """Return the complete event log in ascending sequence order."""
        with self._lock:
            return self._fetch(
                "SELECT sequence_id, event_time, event_type, payload, lamport_clock, prev_hash, event_hash "
                "FROM ledger ORDER BY sequence_id ASC"
            )

    def read_since(self, after_sequence_id: int) -> list[LedgerEvent]:
        """Return events strictly after *after_sequence_id* (for incremental replay)."""
        with self._lock:
            return self._fetch(
                "SELECT sequence_id, event_time, event_type, payload, lamport_clock, prev_hash, event_hash "
                "FROM ledger WHERE sequence_id > ? ORDER BY sequence_id ASC",
                (after_sequence_id,),
            )

    def event_time_at(self, sequence_id: int) -> datetime | None:
        """
        Return the event_time stored at a given sequence_id.
        Used by the transport layer for event-time TTL computation — never wall-clock.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT event_time FROM ledger WHERE sequence_id = ?", (sequence_id,)
            )
            row = cursor.fetchone()
            return datetime.fromisoformat(row[0]) if row else None

    def find_authorization_resolution(self, request_id: str) -> LedgerEvent | None:
        """
        Check whether an authorization request has already been resolved.
        Used by the transport layer's single-resolver protocol.
        """
        with self._lock:
            rows = self._fetch(
                "SELECT sequence_id, event_time, event_type, payload, lamport_clock, prev_hash, event_hash "
                "FROM ledger "
                "WHERE event_type = ? AND json_extract(payload, '$.request_id') = ? "
                "LIMIT 1",
                (EventType.AUTHORIZATION_RESOLVED.value, request_id),
            )
            return rows[0] if rows else None

    # ── Chain verification ────────────────────────────────────────────────────

    def verify_chain(self) -> None:
        """
        Cryptographically verify the entire hash chain from genesis.
        O(n) scan — call at startup and after any suspected tampering.
        Raises LedgerIntegrityError on the first violation found.
        """
        with self._lock:
            events = self.read_all()
            for i, event in enumerate(events):
                expected_prev = _GENESIS_PREV_HASH if i == 0 else events[i - 1].event_hash
                if event.prev_hash != expected_prev:
                    raise LedgerIntegrityError(
                        f"Hash chain broken at sequence_id={event.sequence_id}: "
                        f"expected prev_hash={expected_prev!r}, stored={event.prev_hash!r}"
                    )
                recomputed = _compute_hash(
                    event.sequence_id, event.event_time,
                    event.event_type, event.payload, event.prev_hash,
                )
                if event.event_hash != recomputed:
                    raise LedgerIntegrityError(
                        f"Event hash mismatch at sequence_id={event.sequence_id}: "
                        f"recomputed={recomputed!r}, stored={event.event_hash!r}"
                    )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _tip(self) -> LedgerEvent | None:
        rows = self._fetch(
            "SELECT sequence_id, event_time, event_type, payload, lamport_clock, prev_hash, event_hash "
            "FROM ledger ORDER BY sequence_id DESC LIMIT 1"
        )
        return rows[0] if rows else None

    def _fetch(self, sql: str, params: tuple = ()) -> list[LedgerEvent]:
        cursor = self._conn.execute(sql, params)
        return [
            LedgerEvent(
                sequence_id=row[0],
                event_time=datetime.fromisoformat(row[1]),
                event_type=EventType(row[2]),
                payload=json.loads(row[3]),
                lamport_clock=row[4],
                prev_hash=row[5],
                event_hash=row[6],
            )
            for row in cursor.fetchall()
        ]


# ── Canonical Reducer ─────────────────────────────────────────────────────────

def _initial_snapshot() -> SystemSnapshot:
    return SystemSnapshot()


def _apply(snapshot: SystemSnapshot, event: LedgerEvent) -> SystemSnapshot:
    """
    Pure function. Applies a single ledger event to the current snapshot.
    Returns a new immutable snapshot; the input is never mutated.
    This function must remain a total function — it handles every EventType.
    """
    updates: dict[str, Any] = {
        "ledger_tip_hash": event.event_hash,
        "sequence_id":     event.sequence_id,
        "lamport_clock":   event.lamport_clock,
        "event_count":     snapshot.event_count + 1,
    }

    match event.event_type:
        case EventType.TASK_CREATED:
            p = TaskCreatedPayload(**event.payload)
            updates["task_id"]   = p.task_id
            updates["fsm_state"] = FSMState.IDLE

        case EventType.FSM_TRANSITIONED:
            p = FSMTransitionedPayload(**event.payload)
            updates["fsm_state"] = p.to_state

        case EventType.COGNITION_PROPOSED:
            p = CognitionProposedPayload(**event.payload)
            updates["current_proposal"] = p.proposal

        case EventType.POLICY_EVALUATED:
            p = PolicyEvaluatedPayload(**event.payload)
            if p.decision.decision == PolicyDecisionType.DENIED:
                # Denied proposal is cleared — LLM will receive a new state and re-propose.
                # It does NOT interpret the denial; the FSM transitions and re-enters COGNITION.
                updates["current_proposal"] = None

        case EventType.AUTHORIZATION_REQUESTED:
            p = AuthorizationRequestedPayload(**event.payload)
            updates["pending_authorization"] = p

        case EventType.AUTHORIZATION_RESOLVED:
            # Resolution clears the pending request regardless of outcome.
            updates["pending_authorization"] = None

        case EventType.SIDE_EFFECT_REGISTERED:
            p = SideEffectRegisteredPayload(**event.payload)
            updates["active_side_effects"] = snapshot.active_side_effects + (p.side_effect,)

        case EventType.SIDE_EFFECT_COMMITTED:
            p = SideEffectCommittedPayload(**event.payload)
            updates["active_side_effects"] = tuple(
                se.model_copy(update={"status": SideEffectStatus.COMMITTED, "result": p.result})
                if se.idempotency_key == p.idempotency_key else se
                for se in snapshot.active_side_effects
            )

        case EventType.SIDE_EFFECT_ROLLED_BACK:
            p = SideEffectRolledBackPayload(**event.payload)
            updates["active_side_effects"] = tuple(
                se.model_copy(update={"status": SideEffectStatus.ROLLED_BACK})
                if se.idempotency_key == p.idempotency_key else se
                for se in snapshot.active_side_effects
            )

        case EventType.OBSERVATION_RECORDED:
            p = ObservationRecordedPayload(**event.payload)
            updates["last_observation"]  = p.observation
            updates["current_proposal"]  = None   # Consumed by execution

        case EventType.SYSTEM_HALTED:
            updates["fsm_state"] = FSMState.HALTED

        case _:
            # EXECUTION_STARTED, EXECUTION_COMPLETED, EXECUTION_FAILED,
            # COMPENSATION_APPLIED — no snapshot mutation needed; they are
            # purely audit records. The snapshot is updated by the accompanying
            # SIDE_EFFECT_* or OBSERVATION_RECORDED events.
            pass

    return snapshot.model_copy(update=updates)


def reduce(events: list[LedgerEvent]) -> SystemSnapshot:
    """
    Canonical Reducer — the single, authoritative path from event log to state.

    Pure function. Deterministic. Side-effect free.
    Given the same event log, always returns the same snapshot.

    This function is the CONTRACT between the ledger and the rest of the system.
    No other code may construct a SystemSnapshot except by calling this function
    (or _apply() for incremental replay from a known-good checkpoint).
    """
    snapshot = _initial_snapshot()
    for event in events:
        snapshot = _apply(snapshot, event)
    return snapshot
