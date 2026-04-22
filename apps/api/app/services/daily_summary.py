"""Daily summary email for business operators.

Generates a plain-text digest of the day's activity and sends it via
SendGrid to the business's escalation or summary email address.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from ..db import async_execute, get_supabase_client
from ..providers.email import send_email

logger = logging.getLogger(__name__)


async def generate_daily_summary(business_id: str, for_date: date) -> Optional[str]:
    """Build a summary string for the given business + date."""
    client = get_supabase_client()
    if client is None:
        return None

    biz_res = await async_execute(
        client.table("businesses").select("name, escalation, settings").eq("id", business_id).limit(1)
    )
    biz_rows = getattr(biz_res, "data", None) or []
    if not biz_rows:
        return None
    biz = biz_rows[0]
    biz_name = biz.get("name") or "Your Business"

    snap_res = await async_execute(
        client.table("metric_snapshots")
        .select("*")
        .eq("business_id", business_id)
        .eq("metric_date", for_date.isoformat())
        .limit(1)
    )
    snap = (getattr(snap_res, "data", None) or [None])[0]

    open_tasks = await async_execute(
        client.table("tasks")
        .select("task_type", count="exact")
        .eq("business_id", business_id)
        .eq("status", "open")
    )
    open_count = getattr(open_tasks, "count", None) or len(open_tasks.data or [])

    quote_review_res = await async_execute(
        client.table("tasks")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .eq("task_type", "quote_review")
        .eq("status", "open")
    )
    quote_reviews = getattr(quote_review_res, "count", None) or len(quote_review_res.data or [])

    handoff_res = await async_execute(
        client.table("tasks")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .in_("task_type", ["handoff", "callback"])
        .eq("status", "open")
    )
    urgent_handoffs = getattr(handoff_res, "count", None) or len(handoff_res.data or [])

    knowledge_gap_res = await async_execute(
        client.table("tasks")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .eq("task_type", "knowledge_gap")
        .eq("status", "open")
    )
    knowledge_gaps = getattr(knowledge_gap_res, "count", None) or len(knowledge_gap_res.data or [])

    missed = (snap or {}).get("missed_calls", 0)
    recovered = (snap or {}).get("recovered_leads", 0)
    inbound = (snap or {}).get("inbound_leads", 0)
    bookings = (snap or {}).get("bookings", 0)
    quotes_sent = (snap or {}).get("quotes_sent", 0)

    lines = [
        f"Revenue Edge Daily Summary — {biz_name}",
        f"Date: {for_date.isoformat()}",
        "",
        f"1. New inbound opportunities: {inbound}",
        f"2. Missed calls recovered: {recovered} / {missed} missed",
        f"3. Bookings created: {bookings}",
        f"4. Quotes needing review: {quote_reviews}",
        f"5. Urgent human handoffs: {urgent_handoffs}",
        f"6. Knowledge gaps to approve: {knowledge_gaps}",
        f"7. Quotes sent today: {quotes_sent}",
        "",
        f"Total open tasks: {open_count}",
        "",
        "Log in to your dashboard to review and take action.",
    ]

    return "\n".join(lines)


async def send_daily_summary(business_id: str, for_date: date) -> bool:
    """Generate and email the daily summary to the business operator."""
    client = get_supabase_client()
    if client is None:
        return False

    biz_res = await async_execute(
        client.table("businesses").select("name, escalation, settings").eq("id", business_id).limit(1)
    )
    biz_rows = getattr(biz_res, "data", None) or []
    if not biz_rows:
        return False
    biz = biz_rows[0]

    email_to = None
    escalation = biz.get("escalation") or {}
    if isinstance(escalation, dict):
        email_to = escalation.get("email")
    settings = biz.get("settings") or {}
    if not email_to:
        email_to = settings.get("summary_email")
    if not email_to:
        logger.info("No summary email configured for business %s", business_id)
        return False

    body = await generate_daily_summary(business_id, for_date)
    if not body:
        return False

    biz_name = biz.get("name") or "Revenue Edge"
    result = await send_email(
        to_email=email_to,
        subject=f"{biz_name} — Daily Summary for {for_date.isoformat()}",
        body=body,
        metadata={"business_id": business_id, "type": "daily_summary", "date": for_date.isoformat()},
    )

    if result.delivered:
        try:
            await async_execute(
                client.rpc(
                    "enqueue_event",
                    {
                        "p_event_type": "summary.sent",
                        "p_payload": {
                            "business_id": business_id,
                            "date": for_date.isoformat(),
                            "email": email_to,
                        },
                        "p_business_id": business_id,
                        "p_aggregate_type": "business",
                        "p_aggregate_id": business_id,
                        "p_idempotency_key": f"summary:{business_id}:{for_date.isoformat()}",
                    },
                )
            )
        except Exception:
            logger.warning("Failed to emit summary.sent event", exc_info=True)

    return result.delivered


async def run_daily_summaries() -> int:
    """Send daily summaries for all businesses that have it enabled."""
    client = get_supabase_client()
    if client is None:
        return 0

    res = await async_execute(client.table("businesses").select("id, settings"))
    count = 0
    today = date.today()

    for biz in (res.data or []):
        settings = biz.get("settings") or {}
        if not settings.get("daily_summary_enabled"):
            continue
        try:
            if await send_daily_summary(biz["id"], today):
                count += 1
        except Exception:
            logger.exception("Daily summary failed for business %s", biz["id"])

    logger.info("Daily summaries sent: %d", count)
    return count
