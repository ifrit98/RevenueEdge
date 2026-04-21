"""/v1/tasks — list + status updates for the dashboard inbox."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


_VALID_STATUSES = {"open", "in_progress", "blocked", "done", "cancelled"}


class TaskUpdate(BaseModel):
    status: Optional[str] = Field(default=None)
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    description: Optional[str] = None


@router.get("")
async def list_tasks(
    user: dict = Depends(get_business_user),
    status: Optional[str] = Query(default=None),
    task_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    q = (
        client.table("tasks")
        .select("id, type, title, description, status, priority, source_table, source_id, metadata, due_at, created_at")
        .eq("business_id", user["business_id"])
        .order("priority", desc=False)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
    )
    if status:
        q = q.eq("status", status)
    if task_type:
        q = q.eq("type", task_type)
    res = await async_execute(q)
    return {"tasks": res.data or [], "offset": offset, "limit": limit}


@router.patch("/{task_id}")
async def update_task(
    task_id: str, patch: TaskUpdate, user: dict = Depends(get_business_user)
) -> dict:
    updates: dict = {}
    if patch.status is not None:
        if patch.status not in _VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {patch.status}")
        updates["status"] = patch.status
    if patch.priority is not None:
        updates["priority"] = patch.priority
    if patch.description is not None:
        updates["description"] = patch.description

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    res = await async_execute(
        client.table("tasks")
        .update(updates)
        .eq("id", task_id)
        .eq("business_id", user["business_id"])
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="task not found")
    return res.data[0]
