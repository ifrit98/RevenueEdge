"""/v1/channels — CRUD for business channels (phone, SMS, email, web).

Each channel has an ``external_id`` (e.g. a phone number) that the webhook
layer uses to route inbound events to the correct business.

Schema note: ``channels.status`` is an enum (``setup | active | paused | archived``),
not a boolean ``active`` flag.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/channels", tags=["channels"])


class ChannelCreate(BaseModel):
    channel_type: str = Field(..., pattern=r"^(phone|sms|email|web|whatsapp)$")
    external_id: str = Field(..., min_length=1, max_length=100)
    provider: str = Field(default="retell", max_length=50)
    display_name: Optional[str] = Field(None, max_length=200)
    config: Optional[dict] = None


class ChannelUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern=r"^(active|paused|archived)$")
    display_name: Optional[str] = Field(None, max_length=200)
    config: Optional[dict] = None
    external_id: Optional[str] = Field(None, min_length=1, max_length=100)


@router.get("")
async def list_channels(
    channel_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    q = (
        client.table("channels")
        .select("id, channel_type, external_id, provider, display_name, status, config, created_at")
        .eq("business_id", user["business_id"])
    )
    if channel_type:
        q = q.eq("channel_type", channel_type)
    if status:
        q = q.eq("status", status)
    q = q.order("created_at", desc=False)
    res = await async_execute(q)
    return {"channels": res.data or []}


@router.get("/{channel_id}")
async def get_channel(channel_id: str, user: dict = Depends(get_business_user)) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    res = await async_execute(
        client.table("channels")
        .select("id, channel_type, external_id, provider, display_name, status, config, created_at")
        .eq("id", channel_id)
        .eq("business_id", user["business_id"])
        .limit(1)
    )
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Channel not found")
    return res.data[0]


@router.post("", status_code=201)
async def create_channel(
    body: ChannelCreate,
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    row: dict = {
        "business_id": user["business_id"],
        "channel_type": body.channel_type,
        "external_id": body.external_id,
        "provider": body.provider,
        "config": body.config or {},
    }
    if body.display_name:
        row["display_name"] = body.display_name
    res = await async_execute(client.table("channels").insert(row))
    created = (res.data or [None])[0]
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create channel")
    return created


@router.patch("/{channel_id}")
async def update_channel(
    channel_id: str,
    body: ChannelUpdate,
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = await async_execute(
        client.table("channels")
        .update(patch)
        .eq("id", channel_id)
        .eq("business_id", user["business_id"])
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Channel not found")
    return rows[0]


@router.delete("/{channel_id}", status_code=204)
async def archive_channel(
    channel_id: str,
    user: dict = Depends(get_business_user),
) -> None:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    await async_execute(
        client.table("channels")
        .update({"status": "archived", "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", channel_id)
        .eq("business_id", user["business_id"])
    )
