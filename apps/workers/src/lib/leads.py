"""Leads lifecycle helpers.

Bridges between conversation-intelligence decisions and the ``leads`` table.

Lead stages: new → contacted → qualified → proposal → won | lost
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..supabase_client import async_execute, get_client

logger = logging.getLogger(__name__)

VALID_STAGES = {"new", "contacted", "qualified", "proposal", "won", "lost"}


async def find_or_create_lead(
    *,
    business_id: str,
    contact_id: str,
    conversation_id: str,
    source: str = "inbound_call",
    initial_intent: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> dict:
    """Return the open lead for the contact, or create one at stage ``new``."""
    client = get_client()
    res = await async_execute(
        client.table("leads")
        .select("*")
        .eq("business_id", business_id)
        .eq("contact_id", contact_id)
        .not_.is_("stage", "won")
        .not_.is_("stage", "lost")
        .order("created_at", desc=True)
        .limit(1)
    )
    rows = getattr(res, "data", None) or []
    if rows:
        return rows[0]

    insert_payload: dict[str, Any] = {
        "business_id": business_id,
        "contact_id": contact_id,
        "conversation_id": conversation_id,
        "source": source,
        "stage": "new",
        "metadata": {},
    }
    if initial_intent:
        insert_payload["metadata"]["initial_intent"] = initial_intent
    if trace_id:
        insert_payload["metadata"]["trace_id"] = trace_id

    res = await async_execute(
        client.table("leads").insert(insert_payload)
    )
    rows = getattr(res, "data", None) or []
    if not rows:
        raise RuntimeError("Failed to create lead")
    logger.info("Created lead %s for contact %s", rows[0]["id"], contact_id)
    return rows[0]


async def advance_lead_stage(
    *,
    lead_id: str,
    new_stage: str,
    metadata_patch: Optional[dict] = None,
) -> Optional[dict]:
    """Advance a lead to a new stage, appending any metadata."""
    if new_stage not in VALID_STAGES:
        logger.warning("Invalid lead stage %s, ignoring advance", new_stage)
        return None

    client = get_client()
    update: dict[str, Any] = {
        "stage": new_stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if metadata_patch:
        res = await async_execute(
            client.table("leads").select("metadata").eq("id", lead_id).limit(1)
        )
        existing = {}
        rows = getattr(res, "data", None) or []
        if rows:
            existing = rows[0].get("metadata") or {}
        existing.update(metadata_patch)
        update["metadata"] = existing

    if new_stage in {"won", "lost"}:
        update["closed_at"] = datetime.now(timezone.utc).isoformat()

    res = await async_execute(
        client.table("leads").update(update).eq("id", lead_id)
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


async def upsert_intake_fields(
    *,
    lead_id: str,
    fields: dict,
) -> None:
    """Merge key-value pairs into ``leads.intake_fields``."""
    client = get_client()
    res = await async_execute(
        client.table("leads").select("intake_fields").eq("id", lead_id).limit(1)
    )
    existing: dict = {}
    rows = getattr(res, "data", None) or []
    if rows:
        existing = rows[0].get("intake_fields") or {}
    existing.update(fields)
    await async_execute(
        client.table("leads").update({
            "intake_fields": existing,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", lead_id)
    )
