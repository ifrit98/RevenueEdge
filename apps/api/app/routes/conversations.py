"""/v1/conversations — list/detail views for the dashboard."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(
    user: dict = Depends(get_business_user),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    q = (
        client.table("conversations")
        .select(
            "id, business_id, contact_id, channel_type, status, current_intent, urgency, ai_confidence, summary, last_message_at, created_at"
        )
        .eq("business_id", user["business_id"])
        .order("last_message_at", desc=True)
        .range(offset, offset + limit - 1)
    )
    if status:
        q = q.eq("status", status)
    res = await async_execute(q)
    return {"conversations": res.data or [], "offset": offset, "limit": limit}


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str, user: dict = Depends(get_business_user)
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    conv = await async_execute(
        client.table("conversations")
        .select("*")
        .eq("id", conversation_id)
        .eq("business_id", user["business_id"])
        .limit(1)
    )
    if not conv.data:
        raise HTTPException(status_code=404, detail="conversation not found")
    conversation = conv.data[0]

    messages = await async_execute(
        client.table("messages")
        .select(
            "id, direction, sender_type, body, external_message_id, model_name, created_at"
        )
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
        .limit(200)
    )
    contact = None
    if conversation.get("contact_id"):
        c = await async_execute(
            client.table("contacts")
            .select("id, name, phone_e164, email, tags, metadata")
            .eq("id", conversation["contact_id"])
            .limit(1)
        )
        contact = (c.data or [None])[0]

    return {
        "conversation": conversation,
        "messages": messages.data or [],
        "contact": contact,
    }
