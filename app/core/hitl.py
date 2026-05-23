from __future__ import annotations

import asyncio
import logging

from dataclasses import dataclass, field

logger = logging.getLogger("wade.hitl")

APPROVAL_TIMEOUT = 300.0 

@dataclass
class PendingApproval:
    task_id:   str
    tool_name: str
    args_json: str
    tier:      str
    event:     asyncio.Event = field(default_factory=asyncio.Event)
    approved:  bool | None   = None

_pending: dict[str, PendingApproval] = {}

async def wait_for_decision(
    task_id:   str,
    tool_name: str,
    args_json: str,
    tier:      str,
    timeout:   float = APPROVAL_TIMEOUT,
) -> bool:
    """Suspend execution until a decision is made for the given task_id, or timeout occurs."""
    entry = PendingApproval(
        task_id=task_id, tool_name=tool_name, args_json=args_json, tier=tier
    )
    _pending[task_id] = entry
    try:
        await asyncio.wait_for(entry.event.wait(), timeout=timeout)
        return entry.approved is True
    except asyncio.TimeoutError:
        logger.warning(
            "[HITL] Approval for task %s timed out after %.0fs — auto-rejecting",
            task_id, timeout,
        )
        return False
    finally:
        _pending.pop(task_id, None)

def resolve(task_id: str, approved: bool) -> bool:
    """Set the approval result for a pending task. Returns True if the task was found and updated, False otherwise."""
    entry = _pending.get(task_id)
    if entry is None:
        return False
    entry.approved = approved
    entry.event.set()
    return True

def get_pending(task_id: str) -> PendingApproval | None:
    """Return the pending approval entry for a task, or None."""
    return _pending.get(task_id)