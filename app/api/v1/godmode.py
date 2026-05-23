from __future__ import annotations

import asyncio
import logging

from typing import Any
from fastapi import APIRouter, HTTPException, Depends

from app.core.security import require_admin

logger = logging.getLogger("wade.godmode")

router = APIRouter(
    prefix="/api/godmode",
    tags=["godmode"],
    dependencies=[Depends(require_admin)],
)

def _get_orchestrator():
    from app.core.orchestrator import orchestrator
    return orchestrator

def _get_telemetry():
    from app.core.orchestrator import orchestrator
    return orchestrator._telemetry

@router.get("/traces/{task_id}")
async def get_task_traces(task_id: str) -> dict[str, Any]:
    orc = _get_orchestrator()
    task = orc._store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    subtasks = orc._store.get_children(task_id)

    telemetry = _get_telemetry()

    def _task_dict(t) -> dict:
        return {
            "id": t.id,
            "goal": t.goal,
            "status": t.status.value,
            "parent_id": t.parent_id,
            "created_at": t.created_at.isoformat(),
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "result": t.result,
            "depends_on": t.depends_on,
            "expected_outcome": t.expected_outcome,
        }

    subtask_data = []
    root_verdicts: list[dict] = []

    if telemetry:
        all_verdicts = await asyncio.to_thread(telemetry.get_verdicts, task_id)
        root_verdicts = [v for v in all_verdicts if v.get("step_task_id") is None]
        for st in subtasks:
            traces = await asyncio.to_thread(telemetry.get_traces, st.id)
            step_verdicts = [v for v in all_verdicts if v.get("step_task_id") == st.id]
            subtask_data.append({**_task_dict(st), "traces": traces, "verdicts": step_verdicts})
    else:
        subtask_data = [_task_dict(st) for st in subtasks]

    return {
        "task": _task_dict(task),
        "subtasks": subtask_data,
        "root_verdicts": root_verdicts,
    }

@router.get("/metrics/live")
async def get_live_metrics() -> dict[str, Any]:
    telemetry = _get_telemetry()
    if telemetry is None:
        return {"by_role": {}, "recent": [], "totals": {"prompt_tokens": 0, "completion_tokens": 0, "call_count": 0}}

    recent = await asyncio.to_thread(telemetry.get_recent_metrics, 100)

    by_role: dict[str, dict] = {}
    total_prompt = 0
    total_completion = 0

    for row in recent:
        role = row["role"]
        if role not in by_role:
            by_role[role] = {
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "call_count": 0,
                "total_latency_ms": 0,
            }
        by_role[role]["total_prompt_tokens"]     += row["prompt_tokens"]
        by_role[role]["total_completion_tokens"] += row["completion_tokens"]
        by_role[role]["call_count"]              += 1
        by_role[role]["total_latency_ms"]        += row["latency_ms"]
        total_prompt     += row["prompt_tokens"]
        total_completion += row["completion_tokens"]

    for role_data in by_role.values():
        c = role_data["call_count"]
        role_data["avg_latency_ms"] = role_data["total_latency_ms"] / c if c else 0
        del role_data["total_latency_ms"]

    return {
        "by_role": by_role,
        "recent": recent[:20],
        "totals": {
            "prompt_tokens":     total_prompt,
            "completion_tokens": total_completion,
            "call_count":        len(recent),
        },
    }

@router.post("/tasks/{task_id}/replay")
async def replay_task(task_id: str) -> dict[str, str]:
    from app.core.task_store import Task, TaskStatus

    orc = _get_orchestrator()
    original = orc._store.get(task_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Task not found")

    replayable = {
        TaskStatus.FAILED,
        TaskStatus.INVALID_PLAN,
        TaskStatus.GOAL_NOT_SATISFIED,
        TaskStatus.CANCELLED,
        TaskStatus.TOOL_MISMATCH,
    }
    if original.status not in replayable:
        raise HTTPException(
            status_code=400,
            detail=f"Task status '{original.status.value}' is not replayable",
        )

    new_task = Task(goal=original.goal, created_by="replay")
    await orc.submit(new_task)
    return {"status": "queued", "new_task_id": new_task.id, "replayed_from": task_id}