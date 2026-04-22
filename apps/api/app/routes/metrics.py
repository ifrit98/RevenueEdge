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


@router.get("/comparison")
async def metrics_comparison(
    user: dict = Depends(get_business_user),
    baseline_start: Optional[date] = Query(None),
    baseline_end: Optional[date] = Query(None),
    comparison_start: Optional[date] = Query(None),
    comparison_end: Optional[date] = Query(None),
) -> dict:
    """Return before/after comparison of key metrics for ROI dashboard.

    If baseline dates aren't provided, uses the 30 days before the first
    event for this business (or manual baseline from settings).
    """
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    biz_id = user["business_id"]

    if not comparison_start:
        comparison_start = date.today() - timedelta(days=30)
    if not comparison_end:
        comparison_end = date.today()

    if not baseline_start or not baseline_end:
        biz_res = await async_execute(
            client.table("businesses").select("settings").eq("id", biz_id).limit(1)
        )
        biz_rows = getattr(biz_res, "data", None) or []
        settings = (biz_rows[0].get("settings") or {}) if biz_rows else {}
        manual_baseline = settings.get("baseline_metrics")
        if manual_baseline:
            return _manual_comparison(manual_baseline, biz_id, comparison_start, comparison_end)

        first_event = await async_execute(
            client.table("events")
            .select("occurred_at")
            .eq("business_id", biz_id)
            .order("occurred_at", desc=False)
            .limit(1)
        )
        fe_rows = getattr(first_event, "data", None) or []
        if fe_rows:
            first_date = datetime.fromisoformat(
                fe_rows[0]["occurred_at"].replace("Z", "+00:00")
            ).date()
            baseline_end = first_date - timedelta(days=1)
            baseline_start = baseline_end - timedelta(days=30)
        else:
            return {
                "comparison": [],
                "note": "No baseline data available. Set baseline_metrics in business settings or provide dates.",
            }

    baseline_rows = await _fetch_snapshots(client, biz_id, baseline_start, baseline_end)
    comparison_rows = await _fetch_snapshots(client, biz_id, comparison_start, comparison_end)

    metrics_to_compare = [
        ("missed_calls", "lower_is_better"),
        ("recovered_leads", "higher_is_better"),
        ("inbound_leads", "higher_is_better"),
        ("qualified_leads", "higher_is_better"),
        ("quotes_sent", "higher_is_better"),
        ("bookings", "higher_is_better"),
        ("wins", "higher_is_better"),
        ("attributed_revenue", "higher_is_better"),
    ]

    result = []
    for metric, direction_rule in metrics_to_compare:
        b_avg = _average(baseline_rows, metric)
        c_avg = _average(comparison_rows, metric)
        delta_pct = ((c_avg - b_avg) / b_avg * 100) if b_avg else None
        if direction_rule == "lower_is_better":
            direction = "improved" if c_avg < b_avg else ("unchanged" if c_avg == b_avg else "regressed")
        else:
            direction = "improved" if c_avg > b_avg else ("unchanged" if c_avg == b_avg else "regressed")
        result.append({
            "metric": metric,
            "baseline_avg": round(b_avg, 2),
            "comparison_avg": round(c_avg, 2),
            "delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
            "direction": direction,
        })

    return {
        "comparison": result,
        "baseline_period": {"start": baseline_start.isoformat(), "end": baseline_end.isoformat()},
        "comparison_period": {"start": comparison_start.isoformat(), "end": comparison_end.isoformat()},
    }


async def _fetch_snapshots(client, biz_id: str, start: date, end: date) -> list[dict]:
    res = await async_execute(
        client.table("metric_snapshots")
        .select("*")
        .eq("business_id", biz_id)
        .gte("metric_date", start.isoformat())
        .lte("metric_date", end.isoformat())
    )
    return getattr(res, "data", None) or []


def _average(rows: list[dict], metric: str) -> float:
    if not rows:
        return 0.0
    total = sum(row.get(metric) or 0 for row in rows)
    return total / len(rows)


def _manual_comparison(baseline: dict, biz_id: str, comp_start: date, comp_end: date) -> dict:
    return {
        "comparison": [
            {
                "metric": k.replace("avg_daily_", ""),
                "baseline_avg": v,
                "comparison_avg": None,
                "delta_pct": None,
                "direction": "baseline_only",
            }
            for k, v in baseline.items()
            if isinstance(v, (int, float))
        ],
        "note": "Manual baseline. Comparison data will populate after rollups run.",
    }
