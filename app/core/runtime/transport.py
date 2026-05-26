"""
Exactly-Once Transport Layer — Phase 6 of the architectural specification.

Guarantees:
- LamportClock provides global causal ordering for all events in a single-node
  deployment. Extend to VectorClock for multi-node / distributed deployments.
- AuthorizationResolver enforces exactly-once resolution of HITL requests.
  The protocol:
    1. Check in-memory resolved set (O(1) fast path).
    2. Check the ledger for an existing AUTHORIZATION_RESOLVED event (authoritative).
    3. Check for concurrent in-flight resolution (prevents races within one process).
    4. Check event-time TTL (derived from ledger timestamps, not wall-clock).
    5. Only if all checks pass: return True (caller may write the resolution event).
    6. After the ledger write succeeds, call confirm_resolved() to commit the ID.
    7. If the ledger write fails, call abort_resolution() to release the in-flight lock.

Event-time TTL:
  The TTL window for an authorization request is derived from:
    event_time(AUTHORIZATION_REQUESTED) + ttl_seconds
  compared against:
    event_time(current ledger tip)
  — NOT against datetime.now(). This guarantees that replaying the ledger
  produces the same timeout outcomes regardless of when replay occurs.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from app.core.runtime.schemas import EventType

logger = logging.getLogger("wade.runtime.transport")


# ── Lamport Clock ─────────────────────────────────────────────────────────────

class LamportClock:
    """
    Thread-safe Lamport logical clock.

    Rules:
    - tick()   before sending any event (increments local clock).
    - update() on receiving any event (synchronises with sender's clock).
    - The value is monotonically increasing and never decreases.

    To upgrade to vector clocks for multi-node ordering:
    - Replace _value: int with _vector: dict[str, int]
    - tick(node_id) increments vector[node_id]
    - update(received_vector) takes element-wise max, then increments local
    """

    def __init__(self, initial: int = 0) -> None:
        self._value = initial
        self._lock  = threading.Lock()

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def tick(self) -> int:
        """Increment before sending. Returns the new clock value."""
        with self._lock:
            self._value += 1
            return self._value

    def update(self, received: int) -> int:
        """Synchronise on receive: max(local, received) + 1. Returns new value."""
        with self._lock:
            self._value = max(self._value, received) + 1
            return self._value

    def __repr__(self) -> str:
        return f"LamportClock({self._value})"


# ── Transport Message ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TransportMessage:
    """
    Causal envelope for all events crossing process or thread boundaries.
    The lamport_clock field is mandatory — messages without it cannot be
    causally ordered and must be rejected.
    """
    message_id:     str
    event_type:     EventType
    payload:        dict[str, Any]
    lamport_clock:  int           # Sender's clock at send time
    source:         str           # Logical process ID: "fsm", "ui", "daemon", "timer"
    correlation_id: str | None    # Links request → response (e.g. authz_request → resolution)


# ── Authorization Resolver (Exactly-Once) ─────────────────────────────────────

EventTimeLookup = Callable[[int], datetime | None]


class AuthorizationResolver:
    """
    Enforces the exactly-once resolution guarantee for HITL authorization requests.

    Thread-safety: asyncio.Lock — all operations are async-safe.
    The in-memory sets (_resolved, _in_flight) are caches; the ledger is always
    authoritative. On process restart, the ledger is replayed to rebuild these sets.
    """

    def __init__(self, ledger) -> None:
        self._ledger              = ledger
        self._resolved: set[str]  = set()     # request_ids confirmed in ledger
        self._in_flight: set[str] = set()     # request_ids currently being written
        self._lock                = asyncio.Lock()

    def rebuild_from_ledger(self) -> None:
        """
        Reconstruct the resolved set from the ledger on startup.
        Must be called before the resolver begins accepting resolution attempts.
        """
        events = self._ledger.read_all()
        for event in events:
            if event.event_type == EventType.AUTHORIZATION_RESOLVED:
                rid = event.payload.get("request_id")
                if rid:
                    self._resolved.add(rid)
        logger.info(
            "[TRANSPORT] Resolver rebuilt from ledger: %d resolved request(s).",
            len(self._resolved),
        )

    async def try_resolve(
        self,
        request_id:        str,
        resolution:        str,   # "APPROVED" | "REJECTED"
        resolver_id:       str,
        resolver_clock:    int,
        requested_at_seq:  int,   # sequence_id of the AUTHORIZATION_REQUESTED event
        ttl_seconds:       int,
        event_time_lookup: EventTimeLookup,   # ledger.event_time_at
        current_tip_seq:   int,               # current ledger sequence_id
    ) -> bool:
        """
        Attempt to resolve an authorization request.

        Returns True  → first valid resolution (caller MUST write to ledger then call confirm_resolved).
        Returns False → duplicate, stale, timed-out, or concurrent resolution (caller MUST drop).

        The caller is responsible for the ledger write — this method only gates access.
        If the ledger write fails, call abort_resolution() to release the in-flight slot.
        """
        async with self._lock:
            # Fast path: in-memory resolved cache
            if request_id in self._resolved:
                logger.warning(
                    "[TRANSPORT] Resolution dropped — already resolved: request_id=%r",
                    request_id,
                )
                return False

            # Concurrent resolution guard (within-process races)
            if request_id in self._in_flight:
                logger.warning(
                    "[TRANSPORT] Resolution dropped — concurrent attempt: request_id=%r",
                    request_id,
                )
                return False

            # Authoritative ledger check (catches cross-process or post-restart duplicates)
            existing = self._ledger.find_authorization_resolution(request_id)
            if existing is not None:
                self._resolved.add(request_id)
                logger.warning(
                    "[TRANSPORT] Resolution dropped — ledger already contains resolution: "
                    "request_id=%r at seq=%d",
                    request_id, existing.sequence_id,
                )
                return False

            # Event-time TTL check (NOT wall-clock — purely from ledger timestamps)
            requested_at = event_time_lookup(requested_at_seq)
            current_tip  = event_time_lookup(current_tip_seq)

            if requested_at is not None and current_tip is not None:
                elapsed = (current_tip - requested_at).total_seconds()
                if elapsed > ttl_seconds:
                    logger.warning(
                        "[TRANSPORT] Resolution dropped — event-time TTL expired: "
                        "request_id=%r elapsed=%.1fs ttl=%ds",
                        request_id, elapsed, ttl_seconds,
                    )
                    return False

            # Reserve the in-flight slot
            self._in_flight.add(request_id)
            logger.debug(
                "[TRANSPORT] Resolution gated — request_id=%r resolver=%r",
                request_id, resolver_id,
            )
            return True

    async def confirm_resolved(self, request_id: str) -> None:
        """
        Call after a successful ledger write.
        Moves the request_id from in-flight to resolved.
        """
        async with self._lock:
            self._in_flight.discard(request_id)
            self._resolved.add(request_id)
        logger.info(
            "[TRANSPORT] Resolution confirmed in ledger: request_id=%r", request_id
        )

    async def abort_resolution(self, request_id: str) -> None:
        """
        Call if the ledger write fails after try_resolve() returned True.
        Releases the in-flight slot so a retry is possible.
        """
        async with self._lock:
            self._in_flight.discard(request_id)
        logger.warning(
            "[TRANSPORT] Resolution aborted (ledger write failed): request_id=%r",
            request_id,
        )

    @property
    def resolved_count(self) -> int:
        return len(self._resolved)

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)
