"""/v1/businesses — list businesses the caller belongs to + get details."""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/businesses", tags=["businesses"])


@router.get("")
async def list_businesses(user: dict = Depends(get_business_user)) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    memberships = await async_execute(
        client.table("business_members")
        .select("business_id, role")
        .eq("user_id", user["user_id"])
    )
    ids: List[str] = [row["business_id"] for row in (memberships.data or [])]
    if not ids:
        return {"businesses": []}

    biz = await async_execute(
        client.table("businesses")
        .select("id, name, slug, vertical, timezone, status, created_at")
        .in_("id", ids)
        .order("created_at", desc=False)
    )
    return {"businesses": biz.data or []}


@router.get("/{business_id}")
async def get_business(business_id: str, user: dict = Depends(get_business_user)) -> dict:
    if business_id != user["business_id"]:
        raise HTTPException(status_code=403, detail="Not a member of this business")
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    res = await async_execute(
        client.table("businesses")
        .select("id, name, slug, vertical, timezone, status, service_area, hours, escalation, settings, created_at, updated_at")
        .eq("id", business_id)
        .limit(1)
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="business not found")
    return res.data[0]
