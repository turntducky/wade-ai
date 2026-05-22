from __future__ import annotations

import asyncio

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends

from app.core.security import require_admin

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

class ApproveBody(BaseModel):
    approved: bool

@router.post("/{task_id}/approve")
async def approve_task_action(
    task_id: str,
    body: ApproveBody,
    _: None = Depends(require_admin),
) -> dict:
    """Approve or reject a pending human-in-the-loop action for a task. The task must be awaiting approval, and the caller must be an admin."""
    from app.core import hitl

    entry = hitl.get_pending(task_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No pending approval for this task")

    try:
        from app.main import _telemetry  # type: ignore[attr-defined]
        if _telemetry is not None:
            await asyncio.to_thread(
                _telemetry.log_audit,
                task_id,
                entry.tool_name,
                entry.args_json,
                entry.tier,
                body.approved,
            )
    except Exception:
        pass

    resolved = hitl.resolve(task_id, body.approved)
    if not resolved:
        pass

    return {"status": "resolved", "approved": body.approved}