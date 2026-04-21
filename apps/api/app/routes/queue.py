"""Internal queue endpoints. Used by `re-webhooks` and operator tools to
enqueue jobs and by workers to inspect dead-letter state.

All endpoints require `X-Internal-Key` — callers are service-to-service, not
end users.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_internal_key
from ..db import async_execute, get_supabase_client, rpc

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/queue",
    tags=["queue"],
    dependencies=[Depends(require_internal_key)],
)


class EnqueueJobRequest(BaseModel):
    queue_name: str = Field(..., min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    business_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    priority: int = 100
    max_attempts: int = 5


class EnqueueJobResponse(BaseModel):
    job_id: str


@router.post("/enqueue", response_model=EnqueueJobResponse)
async def enqueue_job(req: EnqueueJobRequest) -> EnqueueJobResponse:
    """Thin wrapper around the `public.enqueue_job` RPC."""
    try:
        result = await rpc(
            "enqueue_job",
            {
                "p_queue_name": req.queue_name,
                "p_payload": req.payload,
                "p_business_id": req.business_id,
                "p_priority": req.priority,
                "p_idempotency_key": req.idempotency_key,
                "p_max_attempts": req.max_attempts,
            },
        )
    except Exception as exc:
        logger.exception("enqueue_job RPC failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to enqueue job")

    data = getattr(result, "data", None)
    if not data:
        raise HTTPException(status_code=502, detail="RPC returned no job id")
    job_id = data if isinstance(data, str) else data[0] if isinstance(data, list) else None
    if not job_id:
        raise HTTPException(status_code=502, detail="RPC returned no job id")
    return EnqueueJobResponse(job_id=job_id)


@router.get("/dead-letter/count")
async def dead_letter_count() -> dict:
    """Count of jobs in dead_letter status, grouped by queue."""
    supabase = get_supabase_client()
    if supabase is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    result = await async_execute(
        supabase.table("queue_jobs")
        .select("queue_name")
        .eq("status", "dead_letter")
    )
    rows = result.data or []
    counts: dict[str, int] = {}
    for row in rows:
        q = row.get("queue_name", "unknown")
        counts[q] = counts.get(q, 0) + 1
    return {"total": len(rows), "by_queue": counts}
