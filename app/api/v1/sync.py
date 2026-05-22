from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import require_admin

router = APIRouter(prefix="/api/sync", tags=["sync"])

@router.get("/state")
async def get_sync_state(
    messages: int = 20,
    _: None = Depends(require_admin),
) -> dict:
    """Get current system state for debugging and telemetry purposes. Includes active tasks, pending HITL actions, and pending proactive messages."""
    from app.core.orchestrator import orchestrator
    from app.core.hitl import _pending as _hitl_pending
    from app.services.proactive import proactive_engine

    active_tasks = [
        {"id": t.id, "goal": t.goal, "status": t.status.value}
        for t in orchestrator.list_recent_tasks(limit=100)
        if t.status.value in ("pending", "planning", "in_progress", "awaiting_approval")
    ]

    hitl_pending = [
        {
            "task_id":   p.task_id,
            "tool_name": p.tool_name,
            "args_json": p.args_json,
            "tier":      p.tier,
        }
        for p in _hitl_pending.values()
    ]

    proactive_msgs = proactive_engine.get_pending_messages(n=messages)

    return {
        "active_tasks":   active_tasks,
        "hitl_pending":   hitl_pending,
        "proactive_msgs": proactive_msgs,
    }