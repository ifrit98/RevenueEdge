"""Leads lifecycle helpers.

Bridges between conversation-intelligence decisions and the ``leads`` table.

Lead stages: new → contacted → qualified → proposal → won | lost
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..supabase_client import async_execute, get_client, rpc

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
    lead = rows[0]
    logger.info("Created lead %s for contact %s", lead["id"], contact_id)
    try:
        await rpc(
            "enqueue_event",
            {
                "p_event_type": "lead.created",
                "p_payload": {
                    "lead_id": lead["id"],
                    "business_id": business_id,
                    "contact_id": contact_id,
                    "source": source,
                    "initial_intent": initial_intent,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "lead",
                "p_aggregate_id": lead["id"],
                "p_idempotency_key": f"lead:created:{lead['id']}",
            },
        )
    except Exception:
        logger.warning("Failed to emit lead.created event", exc_info=True)
    return lead


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
    if not rows:
        return None
    lead = rows[0]
    if new_stage == "qualified":
        try:
            await rpc(
                "enqueue_event",
                {
                    "p_event_type": "lead.qualified",
                    "p_payload": {
                        "lead_id": lead_id,
                        "business_id": lead.get("business_id"),
                        "stage": new_stage,
                    },
                    "p_business_id": lead.get("business_id"),
                    "p_aggregate_type": "lead",
                    "p_aggregate_id": lead_id,
                    "p_idempotency_key": f"lead:qualified:{lead_id}",
                },
            )
        except Exception:
            logger.warning("Failed to emit lead.qualified event", exc_info=True)
    return lead


async def upsert_intake_fields(
    *,
    lead_id: str,
    fields: dict,
    source_message_id: Optional[str] = None,
) -> None:
    """Persist collected fields into ``intake_fields`` rows (one per field)
    and also merge into ``leads.intake_fields`` JSONB for quick access."""
    client = get_client()

    res = await async_execute(
        client.table("leads").select("id, business_id, intake_fields").eq("id", lead_id).limit(1)
    )
    rows = getattr(res, "data", None) or []
    if not rows:
        return
    lead = rows[0]
    business_id = lead.get("business_id")
    existing: dict = lead.get("intake_fields") or {}
    existing.update(fields)

    await async_execute(
        client.table("leads").update({
            "intake_fields": existing,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", lead_id)
    )

    for field_name, field_value in fields.items():
        try:
            await async_execute(
                client.table("intake_fields").upsert(
                    {
                        "business_id": business_id,
                        "lead_id": lead_id,
                        "field_name": field_name,
                        "field_value": str(field_value) if field_value is not None else "",
                        "source_message_id": source_message_id,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    on_conflict="lead_id,field_name",
                )
            )
        except Exception:
            logger.warning("Failed to upsert intake_field %s for lead %s", field_name, lead_id, exc_info=True)


async def check_required_fields_complete(
    *,
    lead_id: str,
    service_id: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """Check if all required intake fields for the lead's service are collected.

    Returns ``(complete, missing_field_names)``.
    """
    client = get_client()
    required: list[str] = []
    if service_id:
        res = await async_execute(
            client.table("services")
            .select("required_intake_fields")
            .eq("id", service_id)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        if rows:
            required = rows[0].get("required_intake_fields") or []

    if not required:
        return True, []

    res = await async_execute(
        client.table("intake_fields")
        .select("field_name, field_value")
        .eq("lead_id", lead_id)
    )
    collected = {
        row["field_name"]
        for row in (getattr(res, "data", None) or [])
        if row.get("field_value")
    }
    missing = [f for f in required if f not in collected]
    return len(missing) == 0, missing
