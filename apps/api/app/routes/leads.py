"""/v1/leads — read leads, update stage, filter by stage/assignee."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/leads", tags=["leads"])

_VALID_STAGES = {"new", "contacted", "qualified", "proposal", "won", "lost"}


class LeadUpdate(BaseModel):
    stage: Optional[str] = Field(None, pattern=r"^(new|contacted|qualified|proposal|won|lost)$")
    assigned_to: Optional[str] = None
    metadata: Optional[dict] = None


@router.get("")
async def list_leads(
    stage: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    q = (
        client.table("leads")
        .select(
            "id, contact_id, conversation_id, source, stage, intake_fields, "
            "metadata, created_at, updated_at, closed_at",
            count="exact",
        )
        .eq("business_id", user["business_id"])
    )
    if stage:
        q = q.eq("stage", stage)
    if assigned_to:
        q = q.eq("assigned_to", assigned_to)
    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    res = await async_execute(q)
    return {
        "leads": res.data or [],
        "total": getattr(res, "count", None) or len(res.data or []),
    }


@router.get("/{lead_id}")
async def get_lead(lead_id: str, user: dict = Depends(get_business_user)) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    res = await async_execute(
        client.table("leads")
        .select("*")
        .eq("id", lead_id)
        .eq("business_id", user["business_id"])
        .limit(1)
    )
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Lead not found")

    lead = res.data[0]
    contact_res = await async_execute(
        client.table("contacts")
        .select("id, name, phone_e164, email")
        .eq("id", lead["contact_id"])
        .limit(1)
    )
    lead["contact"] = (contact_res.data or [None])[0]
    return lead


@router.patch("/{lead_id}")
async def update_lead(
    lead_id: str,
    body: LeadUpdate,
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    patch: dict = {}
    if body.stage is not None:
        patch["stage"] = body.stage
        if body.stage in {"won", "lost"}:
            patch["closed_at"] = datetime.now(timezone.utc).isoformat()
    if body.assigned_to is not None:
        patch["assigned_to"] = body.assigned_to
    if body.metadata is not None:
        existing = await async_execute(
            client.table("leads").select("metadata").eq("id", lead_id).limit(1)
        )
        old_meta = ((existing.data or [{}])[0] or {}).get("metadata") or {}
        old_meta.update(body.metadata)
        patch["metadata"] = old_meta

    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")

    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = await async_execute(
        client.table("leads")
        .update(patch)
        .eq("id", lead_id)
        .eq("business_id", user["business_id"])
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    return rows[0]
