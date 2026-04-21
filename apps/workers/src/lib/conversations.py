"""Conversation + message helpers.

Rules of thumb:
  - One open conversation per (business_id, contact_id, channel_type) at a time.
    If an existing one is 'open' or 'awaiting_*', reuse it; otherwise open new.
  - Every message carries the business_id (denormalized for RLS perf).
  - Inbound messages set `direction='inbound', sender_type='customer'`.
  - Outbound AI messages set `direction='outbound', sender_type='ai'`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..supabase_client import async_execute, get_client

logger = logging.getLogger(__name__)

OPEN_STATUSES = ("open", "awaiting_customer", "awaiting_human")


async def find_or_create_conversation(
    *,
    business_id: str,
    contact_id: Optional[str],
    channel_id: Optional[str],
    channel_type: str,
    initial_status: str = "open",
    metadata: Optional[dict] = None,
) -> Optional[dict]:
    if not business_id or not channel_type:
        return None

    client = get_client()
    if contact_id:
        q = (
            client.table("conversations")
            .select("id, business_id, contact_id, channel_id, channel_type, status, current_intent, urgency, ai_confidence, summary, metadata")
            .eq("business_id", business_id)
            .eq("contact_id", contact_id)
            .eq("channel_type", channel_type)
            .in_("status", list(OPEN_STATUSES))
            .order("last_message_at", desc=True)
            .limit(1)
        )
        res = await async_execute(q)
        rows = getattr(res, "data", None) or []
        if rows:
            return rows[0]

    insert_row: dict[str, Any] = {
        "business_id": business_id,
        "contact_id": contact_id,
        "channel_id": channel_id,
        "channel_type": channel_type,
        "status": initial_status,
        "metadata": metadata or {},
    }
    res = await async_execute(
        client.table("conversations")
        .insert({k: v for k, v in insert_row.items() if v is not None})
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


async def insert_message(
    *,
    business_id: str,
    conversation_id: str,
    contact_id: Optional[str],
    channel_id: Optional[str],
    direction: str,
    sender_type: str,
    body: Optional[str] = None,
    normalized_body: Optional[str] = None,
    attachments: Optional[list[dict]] = None,
    external_message_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    model_name: Optional[str] = None,
    token_usage: Optional[dict] = None,
    raw_payload: Optional[dict] = None,
) -> Optional[dict]:
    client = get_client()
    row: dict[str, Any] = {
        "business_id": business_id,
        "conversation_id": conversation_id,
        "contact_id": contact_id,
        "channel_id": channel_id,
        "direction": direction,
        "sender_type": sender_type,
        "body": body,
        "normalized_body": normalized_body,
        "attachments": attachments or [],
        "external_message_id": external_message_id,
        "idempotency_key": idempotency_key,
        "model_name": model_name,
        "token_usage": token_usage or {},
        "raw_payload": raw_payload or {},
    }
    res = await async_execute(
        client.table("messages")
        .insert({k: v for k, v in row.items() if v is not None})
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


async def load_conversation_context(
    *,
    conversation_id: str,
    message_limit: int = 25,
) -> Optional[dict]:
    """Return {conversation, messages[], contact} for the intelligence worker."""
    client = get_client()
    conv_res = await async_execute(
        client.table("conversations")
        .select("id, business_id, contact_id, channel_id, channel_type, status, current_intent, urgency, ai_confidence, summary, metadata, last_message_at, created_at")
        .eq("id", conversation_id)
        .limit(1)
    )
    conv_rows = getattr(conv_res, "data", None) or []
    if not conv_rows:
        return None
    conv = conv_rows[0]

    msg_res = await async_execute(
        client.table("messages")
        .select("id, direction, sender_type, body, normalized_body, external_message_id, model_name, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
        .limit(message_limit)
    )
    messages = getattr(msg_res, "data", None) or []

    contact = None
    if conv.get("contact_id"):
        c_res = await async_execute(
            client.table("contacts")
            .select("id, name, phone_e164, email, tags, metadata")
            .eq("id", conv["contact_id"])
            .limit(1)
        )
        c_rows = getattr(c_res, "data", None) or []
        contact = c_rows[0] if c_rows else None

    return {"conversation": conv, "messages": messages, "contact": contact}


async def update_conversation(
    *,
    conversation_id: str,
    status: Optional[str] = None,
    current_intent: Optional[str] = None,
    urgency: Optional[str] = None,
    ai_confidence: Optional[float] = None,
    summary: Optional[str] = None,
    metadata_patch: Optional[dict] = None,
) -> None:
    patch: dict[str, Any] = {}
    if status is not None:
        patch["status"] = status
    if current_intent is not None:
        patch["current_intent"] = current_intent
    if urgency is not None:
        patch["urgency"] = urgency
    if ai_confidence is not None:
        patch["ai_confidence"] = ai_confidence
    if summary is not None:
        patch["summary"] = summary
    if metadata_patch:
        # Shallow merge on the client side.
        client = get_client()
        existing = await async_execute(
            client.table("conversations").select("metadata").eq("id", conversation_id).limit(1)
        )
        rows = getattr(existing, "data", None) or []
        base_meta = (rows[0] or {}).get("metadata") if rows else {}
        merged = {**(base_meta or {}), **metadata_patch}
        patch["metadata"] = merged

    if not patch:
        return
    client = get_client()
    await async_execute(
        client.table("conversations").update(patch).eq("id", conversation_id)
    )
