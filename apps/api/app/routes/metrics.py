"""/v1/metrics — read daily metric_snapshots and trigger rollups.

For the MVP the rollup is a simple SQL aggregation over the last 24h of
`events`, `conversations`, and `tasks`. A background asyncio task in
`re-api` runs it every 10 minutes, and this endpoint exposes read/trigger.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import get_business_user, require_internal_key
from ..db import async_execute, get_supabase_client
from ..services.metrics_rollup import run_daily_rollup

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get("")
async def list_snapshots(
    user: dict = Depends(get_business_user),
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    since = (date.today() - timedelta(days=days)).isoformat()
    res = await async_execute(
        client.table("metric_snapshots")
        .select("*")
        .eq("business_id", user["business_id"])
        .gte("metric_date", since)
        .order("metric_date", desc=True)
    )
    return {"snapshots": res.data or []}


class RollupRequest(BaseModel):
    business_id: Optional[str] = None
    for_date: Optional[date] = None


@router.post("/rollup")
async def trigger_rollup(
    body: RollupRequest,
    _: None = Depends(require_internal_key),
) -> dict:
    """Manually trigger a daily rollup. Requires `X-Internal-Key`."""
    target = body.for_date or date.today()
    count = await run_daily_rollup(business_id=body.business_id, metric_date=target)
    return {"ok": True, "metric_date": target.isoformat(), "businesses_updated": count}
