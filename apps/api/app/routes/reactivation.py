"""/v1/reactivation — preview, launch, and track reactivation campaigns.

Reactivation sends a single outreach SMS to stale leads via the
follow-up-scheduler queue, staggered to avoid SMS bursts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/reactivation", tags=["reactivation"])

_DEFAULT_STALE_DAYS = 30
_DEFAULT_STAGGER_SECONDS = 5


class ReactivationFilters(BaseModel):
    stale_days: int = Field(default=30, ge=7, le=365)
    stages: list[str] = Field(default_factory=lambda: ["no_response", "nurture"])
    service_id: Optional[str] = None
    max_leads: int = Field(default=200, ge=1, le=1000)


class LaunchRequest(BaseModel):
    filters: ReactivationFilters = Field(default_factory=ReactivationFilters)
    template_name: str = Field(default="reactivation")


@router.post("/preview")
async def preview_segment(
    body: ReactivationFilters = ReactivationFilters(),
    user: dict = Depends(get_business_user),
) -> dict:
    """Return a count + sample of leads matching the reactivation filters."""
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=body.stale_days)).isoformat()
    q = (
        client.table("leads")
        .select("id, contact_id, conversation_id, service_id, stage, updated_at", count="exact")
        .eq("business_id", user["business_id"])
        .in_("stage", body.stages)
        .lt("updated_at", cutoff)
        .order("updated_at", desc=False)
        .limit(body.max_leads)
    )
    if body.service_id:
        q = q.eq("service_id", body.service_id)

    res = await async_execute(q)
    leads = res.data or []
    total = getattr(res, "count", None) or len(leads)

    sample = leads[:5]
    for s in sample:
        if s.get("contact_id"):
            c_res = await async_execute(
                client.table("contacts")
                .select("id, name, phone_e164")
                .eq("id", s["contact_id"])
                .limit(1)
            )
            s["contact"] = (c_res.data or [None])[0]

    return {"total": total, "sample": sample, "filters_applied": body.model_dump()}


@router.post("/launch")
async def launch_campaign(
    body: LaunchRequest,
    user: dict = Depends(get_business_user),
) -> dict:
    """Create and enqueue a reactivation batch."""
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    f = body.filters
    cutoff = (datetime.now(timezone.utc) - timedelta(days=f.stale_days)).isoformat()
    q = (
        client.table("leads")
        .select("id, contact_id, conversation_id, service_id, stage")
        .eq("business_id", user["business_id"])
        .in_("stage", f.stages)
        .lt("updated_at", cutoff)
        .order("updated_at", desc=False)
        .limit(f.max_leads)
    )
    if f.service_id:
        q = q.eq("service_id", f.service_id)
    res = await async_execute(q)
    leads = res.data or []

    eligible: list[dict] = []
    for lead in leads:
        cid = lead.get("contact_id")
        if not cid:
            continue
        c_res = await async_execute(
            client.table("contacts").select("metadata").eq("id", cid).limit(1)
        )
        c_rows = getattr(c_res, "data", None) or []
        if c_rows and (c_rows[0].get("metadata") or {}).get("sms_opt_out"):
            continue
        eligible.append(lead)

    if not eligible:
        return {"batch_id": None, "total_leads": 0, "enqueued": 0, "message": "No eligible leads"}

    batch_id = str(uuid.uuid4())
    enqueued = 0
    now = datetime.now(timezone.utc)

    for i, lead in enumerate(eligible):
        available_at = (now + timedelta(seconds=i * _DEFAULT_STAGGER_SECONDS)).isoformat()
        try:
            await async_execute(
                client.rpc(
                    "enqueue_job",
                    {
                        "p_queue_name": "follow-up-scheduler",
                        "p_payload": {
                            "followup_type": "reactivation",
                            "lead_id": lead["id"],
                            "contact_id": lead.get("contact_id"),
                            "conversation_id": lead.get("conversation_id"),
                            "business_id": user["business_id"],
                            "template_name": body.template_name,
                            "attempt": 1,
                            "max_attempts": 1,
                            "trace_id": f"reactivation:{batch_id}",
                        },
                        "p_business_id": user["business_id"],
                        "p_idempotency_key": f"react:batch:{batch_id}:{lead['id']}",
                        "p_priority": 50,
                        "p_available_at": available_at,
                    },
                )
            )
            enqueued += 1
        except Exception:
            logger.warning("Failed to enqueue reactivation for lead %s", lead["id"], exc_info=True)

    await async_execute(
        client.rpc(
            "enqueue_event",
            {
                "p_event_type": "reactivation.batch_requested",
                "p_payload": {
                    "batch_id": batch_id,
                    "business_id": user["business_id"],
                    "total_leads": len(eligible),
                    "enqueued": enqueued,
                    "template_name": body.template_name,
                },
                "p_business_id": user["business_id"],
                "p_aggregate_type": "business",
                "p_aggregate_id": user["business_id"],
                "p_idempotency_key": f"react:batch:{batch_id}",
            },
        )
    )

    return {"batch_id": batch_id, "total_leads": len(eligible), "enqueued": enqueued}


@router.get("/{batch_id}")
async def get_batch_status(
    batch_id: str,
    user: dict = Depends(get_business_user),
) -> dict:
    """Return status of a reactivation batch by counting related events."""
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    trace_pattern = f"reactivation:{batch_id}"

    sent_res = await async_execute(
        client.table("events")
        .select("id", count="exact")
        .eq("business_id", user["business_id"])
        .eq("event_type", "reactivation.sent")
        .ilike("payload->>trace_id", f"%{batch_id}%")
    )
    sent = getattr(sent_res, "count", None) or len(sent_res.data or [])

    replied_res = await async_execute(
        client.table("events")
        .select("id", count="exact")
        .eq("business_id", user["business_id"])
        .eq("event_type", "reactivation.replied")
        .ilike("payload->>trace_id", f"%{batch_id}%")
    )
    replied = getattr(replied_res, "count", None) or len(replied_res.data or [])

    return {"batch_id": batch_id, "sent": sent, "replied": replied}


@router.get("")
async def list_batches(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_business_user),
) -> dict:
    """List recent reactivation batch events."""
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    res = await async_execute(
        client.table("events")
        .select("id, payload, occurred_at")
        .eq("business_id", user["business_id"])
        .eq("event_type", "reactivation.batch_requested")
        .order("occurred_at", desc=True)
        .limit(limit)
    )
    batches = []
    for row in (res.data or []):
        p = row.get("payload") or {}
        batches.append({
            "batch_id": p.get("batch_id"),
            "total_leads": p.get("total_leads"),
            "enqueued": p.get("enqueued"),
            "template_name": p.get("template_name"),
            "launched_at": row.get("occurred_at"),
        })
    return {"batches": batches}
