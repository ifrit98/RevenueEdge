"""followup_scheduler — consumes `follow-up-scheduler`.

Handles deferred follow-up checks. Job types:
  - after_hours_review:  When business opens, check if customer replied.
                         If not → create a human review task.
  - no_reply_check:      After N hours since last outbound, check if customer
                         replied. If not → bump attempt or escalate.
  - quote_recovery:      (Phase 3) — check if quote was accepted.
  - reactivation:        (Phase 5) — stale-lead nudge.

Stop conditions (complete as no-op):
  - Customer replied since the followup was scheduled.
  - Lead stage moved to ``won``, ``lost``, or ``booked``.
  - Conversation status is ``closed`` or ``resolved``.
  - Max attempts exceeded.

Escalation:
  - If final attempt with no reply → create a ``followup`` task (human handoff).
  - Optionally re-enqueue self with ``attempt + 1`` and exponential ``delay_until``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..base import BaseWorker, Job, PermanentError
from ..lib.conversations import load_conversation_context
from ..supabase_client import async_execute, get_client, rpc

logger = logging.getLogger(__name__)

_BACKOFF_HOURS = [0, 2, 6, 24]


class FollowupSchedulerWorker(BaseWorker):
    queue_name = "follow-up-scheduler"
    max_concurrency = 2

    async def handle(self, job: Job) -> Optional[dict]:
        payload = job.payload or {}
        conversation_id = payload.get("conversation_id")
        business_id = job.business_id or payload.get("business_id")
        followup_type = payload.get("followup_type") or "no_reply_check"
        attempt = int(payload.get("attempt") or 1)
        max_attempts = int(payload.get("max_attempts") or 4)
        trace_id = payload.get("trace_id")

        if not conversation_id:
            raise PermanentError("conversation_id required")

        ctx = await load_conversation_context(conversation_id=conversation_id)
        if not ctx:
            return {"noop": True, "reason": "conversation_not_found"}

        conv = ctx.get("conversation") or {}
        messages = ctx.get("messages") or []

        if conv.get("status") in {"closed", "resolved"}:
            return {"noop": True, "reason": "conversation_closed"}

        lead = await self._load_lead(business_id, ctx)
        if lead and lead.get("stage") in {"won", "lost", "booked"}:
            return {"noop": True, "reason": f"lead_stage_{lead['stage']}"}

        if self._customer_replied_since(messages, job):
            return {"noop": True, "reason": "customer_replied"}

        if attempt > max_attempts:
            return await self._escalate(
                conversation_id=conversation_id,
                business_id=business_id,
                followup_type=followup_type,
                trace_id=trace_id,
                job_id=job.id,
            )

        # Dispatch per type.
        handler = {
            "after_hours_review": self._after_hours_review,
            "no_reply_check": self._no_reply_check,
        }.get(followup_type)

        if handler:
            return await handler(
                conversation_id=conversation_id,
                business_id=business_id,
                attempt=attempt,
                max_attempts=max_attempts,
                trace_id=trace_id,
                job_id=job.id,
            )

        logger.info(
            "Unhandled followup_type %s — treating as no-op", followup_type,
            extra={"conversation_id": conversation_id, "followup_type": followup_type},
        )
        return {"noop": True, "reason": f"unhandled_type:{followup_type}"}

    async def _after_hours_review(
        self, *, conversation_id: str, business_id: str,
        attempt: int, max_attempts: int, trace_id: Optional[str], job_id: str,
    ) -> dict:
        """Business just opened — no customer reply → create review task."""
        return await self._escalate(
            conversation_id=conversation_id,
            business_id=business_id,
            followup_type="after_hours_review",
            trace_id=trace_id,
            job_id=job_id,
        )

    async def _no_reply_check(
        self, *, conversation_id: str, business_id: str,
        attempt: int, max_attempts: int, trace_id: Optional[str], job_id: str,
    ) -> dict:
        """No reply since outbound — bump attempt or escalate."""
        if attempt >= max_attempts:
            return await self._escalate(
                conversation_id=conversation_id,
                business_id=business_id,
                followup_type="no_reply_check",
                trace_id=trace_id,
                job_id=job_id,
            )

        backoff_idx = min(attempt, len(_BACKOFF_HOURS) - 1)
        delay_hours = _BACKOFF_HOURS[backoff_idx]
        available_at = (datetime.now(timezone.utc) + timedelta(hours=delay_hours)).isoformat()

        await rpc(
            "enqueue_job",
            {
                "p_queue_name": "follow-up-scheduler",
                "p_payload": {
                    "conversation_id": conversation_id,
                    "business_id": business_id,
                    "followup_type": "no_reply_check",
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_idempotency_key": f"fu:nrc:{conversation_id}:{attempt + 1}",
                "p_priority": 40,
                "p_available_at": available_at,
            },
        )

        return {
            "action": "re_enqueued",
            "attempt": attempt + 1,
            "delay_hours": delay_hours,
        }

    async def _escalate(
        self, *, conversation_id: str, business_id: str,
        followup_type: str, trace_id: Optional[str], job_id: str,
    ) -> dict:
        """Final escalation: create a task for human review."""
        client = get_client()
        await async_execute(
            client.table("tasks").insert({
                "business_id": business_id,
                "conversation_id": conversation_id,
                "task_type": "followup",
                "title": f"Follow-up needed ({followup_type}) — no customer reply",
                "priority": "high",
                "status": "open",
                "metadata": {
                    "followup_type": followup_type,
                    "trace_id": trace_id,
                    "escalated_at": datetime.now(timezone.utc).isoformat(),
                },
            })
        )
        await rpc(
            "enqueue_event",
            {
                "p_event_type": "followup.escalated",
                "p_payload": {
                    "conversation_id": conversation_id,
                    "business_id": business_id,
                    "followup_type": followup_type,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "conversation",
                "p_aggregate_id": conversation_id,
                "p_idempotency_key": f"fu:esc:{job_id}",
            },
        )
        return {"action": "escalated", "followup_type": followup_type}

    @staticmethod
    def _customer_replied_since(messages: list[dict], job: Job) -> bool:
        """Check if any inbound customer message arrived after the job was created."""
        job_created = job.raw.get("created_at")
        if not job_created:
            return False
        for m in reversed(messages):
            if m.get("direction") == "inbound" and m.get("sender_type") == "customer":
                return True
        return False

    @staticmethod
    async def _load_lead(business_id: Optional[str], ctx: dict) -> Optional[dict]:
        if not business_id:
            return None
        contact = ctx.get("contact")
        if not contact:
            return None
        client = get_client()
        res = await async_execute(
            client.table("leads")
            .select("id, stage")
            .eq("business_id", business_id)
            .eq("contact_id", contact["id"])
            .order("created_at", desc=True)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
