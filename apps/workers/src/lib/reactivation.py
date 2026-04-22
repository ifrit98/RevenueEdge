"""Reactivation campaign helpers.

Selects stale leads matching configurable filters and creates a batch of
follow-up-scheduler jobs staggered over time to avoid SMS bursts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..supabase_client import async_execute, get_client, rpc

logger = logging.getLogger(__name__)

_DEFAULT_STALE_DAYS = 30
_DEFAULT_STAGGER_SECONDS = 5


async def select_reactivation_segment(
    *,
    business_id: str,
    stale_days: Optional[int] = None,
    stages: Optional[list[str]] = None,
    service_id: Optional[str] = None,
    max_results: int = 200,
) -> list[dict]:
    """Return leads eligible for reactivation outreach."""
    client = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days or _DEFAULT_STALE_DAYS)).isoformat()
    target_stages = stages or ["no_response", "nurture"]

    q = (
        client.table("leads")
        .select("id, contact_id, conversation_id, service_id, stage, updated_at")
        .eq("business_id", business_id)
        .in_("stage", target_stages)
        .lt("updated_at", cutoff)
        .order("updated_at", desc=False)
        .limit(max_results)
    )
    if service_id:
        q = q.eq("service_id", service_id)

    res = await async_execute(q)
    leads = getattr(res, "data", None) or []

    eligible: list[dict] = []
    for lead in leads:
        contact_id = lead.get("contact_id")
        if not contact_id:
            continue
        c_res = await async_execute(
            client.table("contacts")
            .select("id, metadata")
            .eq("id", contact_id)
            .limit(1)
        )
        c_rows = getattr(c_res, "data", None) or []
        if c_rows and (c_rows[0].get("metadata") or {}).get("sms_opt_out"):
            continue
        eligible.append(lead)

    return eligible


async def create_reactivation_batch(
    *,
    business_id: str,
    segment: list[dict],
    template_name: str = "reactivation",
    stagger_seconds: int = _DEFAULT_STAGGER_SECONDS,
) -> dict:
    """Enqueue follow-up-scheduler jobs for each lead in the segment.

    Returns ``{batch_id, total_leads, enqueued, opted_out_skipped}``.
    """
    batch_id = str(uuid.uuid4())
    enqueued = 0
    now = datetime.now(timezone.utc)

    for i, lead in enumerate(segment):
        available_at = (now + timedelta(seconds=i * stagger_seconds)).isoformat()
        try:
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "follow-up-scheduler",
                    "p_payload": {
                        "followup_type": "reactivation",
                        "lead_id": lead["id"],
                        "contact_id": lead.get("contact_id"),
                        "conversation_id": lead.get("conversation_id"),
                        "business_id": business_id,
                        "template_name": template_name,
                        "attempt": 1,
                        "max_attempts": 1,
                        "trace_id": f"reactivation:{batch_id}",
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"react:batch:{batch_id}:{lead['id']}",
                    "p_priority": 50,
                    "p_available_at": available_at,
                },
            )
            enqueued += 1
        except Exception:
            logger.warning("Failed to enqueue reactivation for lead %s", lead["id"], exc_info=True)

    await rpc(
        "enqueue_event",
        {
            "p_event_type": "reactivation.batch_requested",
            "p_payload": {
                "batch_id": batch_id,
                "business_id": business_id,
                "total_leads": len(segment),
                "enqueued": enqueued,
                "template_name": template_name,
            },
            "p_business_id": business_id,
            "p_aggregate_type": "business",
            "p_aggregate_id": business_id,
            "p_idempotency_key": f"react:batch:{batch_id}",
        },
    )

    return {
        "batch_id": batch_id,
        "total_leads": len(segment),
        "enqueued": enqueued,
    }
