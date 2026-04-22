"""Daily metric rollup.

MVP implementation:
  - For each business (or one business if specified), compute a row in
    `public.metric_snapshots` keyed by (business_id, metric_date).
  - Source signals come from `events` + `conversations` + `leads` + `tasks`:

      missed_calls        = events WHERE event_type='inbound.call.missed'
      inbound_leads       = events WHERE event_type LIKE 'inbound.%' (excluding missed + started)
      recovered_leads     = conversations WHERE status IN ('awaiting_customer','resolved','awaiting_human')
                            AND last_message_at on that date
                            AND EXISTS outbound message on same conversation
      qualified_leads     = leads WHERE stage IN ('qualified','quoted','booked','won')
                            AND updated_at::date = metric_date
      quotes_sent         = quotes WHERE sent_at::date = metric_date
      bookings            = bookings WHERE scheduled_start::date = metric_date
      wins                = leads WHERE stage='won' AND updated_at::date = metric_date
      avg_response_seconds = median gap between inbound event and first outbound
                             message for conversations updated on that date

`attributed_revenue` stays 0 in MVP — Phase 3 concern.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)


async def _fetch_business_ids(business_id: Optional[str]) -> list[str]:
    client = get_supabase_client()
    if client is None:
        return []
    if business_id:
        return [business_id]
    res = await async_execute(client.table("businesses").select("id"))
    return [row["id"] for row in (res.data or [])]


def _range_iso(metric_date: date) -> tuple[str, str]:
    start = datetime.combine(metric_date, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


async def _count_events(*, business_id: str, event_type: str, start: str, end: str) -> int:
    client = get_supabase_client()
    if client is None:
        return 0
    res = await async_execute(
        client.table("events")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .eq("event_type", event_type)
        .gte("occurred_at", start)
        .lt("occurred_at", end)
    )
    return int(getattr(res, "count", None) or len(res.data or []))


async def _count_leads_in_stages(
    *, business_id: str, stages: list[str], start: str, end: str
) -> int:
    client = get_supabase_client()
    if client is None:
        return 0
    res = await async_execute(
        client.table("leads")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .in_("stage", stages)
        .gte("updated_at", start)
        .lt("updated_at", end)
    )
    return int(getattr(res, "count", None) or len(res.data or []))


async def _count_quotes_sent(*, business_id: str, start: str, end: str) -> int:
    client = get_supabase_client()
    if client is None:
        return 0
    res = await async_execute(
        client.table("quotes")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .gte("sent_at", start)
        .lt("sent_at", end)
    )
    return int(getattr(res, "count", None) or len(res.data or []))


async def _count_bookings(*, business_id: str, start: str, end: str) -> int:
    client = get_supabase_client()
    if client is None:
        return 0
    res = await async_execute(
        client.table("bookings")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .gte("scheduled_start", start)
        .lt("scheduled_start", end)
    )
    return int(getattr(res, "count", None) or len(res.data or []))


async def _sum_attributed_revenue(*, business_id: str, start: str, end: str) -> float:
    """Sum of quote amount_low for leads that moved to 'won' on the metric date."""
    client = get_supabase_client()
    if client is None:
        return 0.0
    res = await async_execute(
        client.table("leads")
        .select("id")
        .eq("business_id", business_id)
        .eq("stage", "won")
        .gte("updated_at", start)
        .lt("updated_at", end)
    )
    lead_ids = [r["id"] for r in (res.data or [])]
    if not lead_ids:
        return 0.0
    total = 0.0
    for lid in lead_ids:
        q_res = await async_execute(
            client.table("quotes")
            .select("amount_low")
            .eq("lead_id", lid)
            .eq("status", "sent")
            .limit(1)
        )
        for q in (q_res.data or []):
            total += q.get("amount_low") or 0
    return total


async def _count_tasks_by_type(*, business_id: str, task_type: str, start: str, end: str) -> int:
    client = get_supabase_client()
    if client is None:
        return 0
    res = await async_execute(
        client.table("tasks")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .eq("task_type", task_type)
        .gte("created_at", start)
        .lt("created_at", end)
    )
    return int(getattr(res, "count", None) or len(res.data or []))


async def _count_recovered(*, business_id: str, start: str, end: str) -> int:
    """Conversations with at least one outbound message on `metric_date`
    after an inbound message on same or prior day."""
    client = get_supabase_client()
    if client is None:
        return 0
    res = await async_execute(
        client.table("messages")
        .select("conversation_id")
        .eq("business_id", business_id)
        .eq("direction", "outbound")
        .gte("created_at", start)
        .lt("created_at", end)
    )
    conv_ids = sorted({row["conversation_id"] for row in (res.data or [])})
    return len(conv_ids)


async def _rollup_for_business(business_id: str, metric_date: date) -> bool:
    client = get_supabase_client()
    if client is None:
        return False

    start, end = _range_iso(metric_date)

    missed = await _count_events(
        business_id=business_id,
        event_type="inbound.call.missed",
        start=start,
        end=end,
    )
    inbound_ended = await _count_events(
        business_id=business_id,
        event_type="inbound.call.ended",
        start=start,
        end=end,
    )
    inbound_msg = await _count_events(
        business_id=business_id,
        event_type="inbound.message.received",
        start=start,
        end=end,
    )
    inbound_leads = missed + inbound_ended + inbound_msg

    qualified = await _count_leads_in_stages(
        business_id=business_id,
        stages=["qualified", "quoted", "booked", "won"],
        start=start,
        end=end,
    )
    wins = await _count_leads_in_stages(
        business_id=business_id, stages=["won"], start=start, end=end
    )
    quotes_sent = await _count_quotes_sent(business_id=business_id, start=start, end=end)
    bookings = await _count_bookings(business_id=business_id, start=start, end=end)
    recovered = await _count_recovered(business_id=business_id, start=start, end=end)
    attributed_revenue = await _sum_attributed_revenue(business_id=business_id, start=start, end=end)

    knowledge_gaps = await _count_tasks_by_type(
        business_id=business_id, task_type="knowledge_gap", start=start, end=end
    )
    after_hours_leads = await _count_events(
        business_id=business_id,
        event_type="followup.escalated",
        start=start,
        end=end,
    )

    row = {
        "business_id": business_id,
        "metric_date": metric_date.isoformat(),
        "missed_calls": missed,
        "recovered_leads": recovered,
        "inbound_leads": inbound_leads,
        "qualified_leads": qualified,
        "quotes_sent": quotes_sent,
        "bookings": bookings,
        "wins": wins,
        "attributed_revenue": attributed_revenue,
        "payload": {
            "version": 2,
            "knowledge_gaps": knowledge_gaps,
            "after_hours_leads": after_hours_leads,
        },
    }

    # supabase-py upsert via `on_conflict`.
    await async_execute(
        client.table("metric_snapshots")
        .upsert(row, on_conflict="business_id,metric_date")
    )
    return True


async def run_daily_rollup(
    *, business_id: Optional[str] = None, metric_date: Optional[date] = None
) -> int:
    md = metric_date or date.today()
    ids = await _fetch_business_ids(business_id)
    count = 0
    for bid in ids:
        try:
            if await _rollup_for_business(bid, md):
                count += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("Rollup failed for business_id=%s: %s", bid, exc)
    logger.info(
        "Daily rollup done", extra={"metric_date": md.isoformat(), "businesses_updated": count}
    )
    return count
