"""handoff — consumes `human-handoff`.

Creates a `tasks` row of type `human_handoff`, flips the conversation
status to `awaiting_human`, and (optionally) emails operators via
SendGrid when `businesses.escalation.email` is set.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..base import BaseWorker, Job, PermanentError
from ..lib.channels import fetch_business
from ..lib.conversations import update_conversation
from ..providers.email import send_email
from ..settings import get_worker_settings
from ..supabase_client import async_execute, get_client, rpc

logger = logging.getLogger(__name__)


_URGENCY_PRIORITY = {
    "emergency": 1,
    "same_day": 2,
    "soon": 3,
    "routine": 4,
    "unknown": 3,
}


class HandoffWorker(BaseWorker):
    queue_name = "human-handoff"
    max_concurrency = 2

    async def handle(self, job: Job) -> Optional[dict]:
        payload = job.payload or {}
        conversation_id = payload.get("conversation_id")
        business_id = job.business_id or payload.get("business_id")
        trace_id = payload.get("trace_id")

        if not conversation_id or not business_id:
            raise PermanentError("conversation_id and business_id required")

        client = get_client()

        res = await async_execute(
            client.table("conversations")
            .select("id, contact_id, channel_type, current_intent, urgency, summary")
            .eq("id", conversation_id)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            raise PermanentError(f"conversation {conversation_id} not found")
        conv = rows[0]

        urgency = payload.get("urgency") or conv.get("urgency") or "unknown"
        priority = _URGENCY_PRIORITY.get(urgency, 3)
        intent = payload.get("intent") or conv.get("current_intent") or "unknown"
        reason = payload.get("reason") or "AI requested human handoff"
        summary = payload.get("summary") or conv.get("summary") or ""

        contact_label = ""
        if conv.get("contact_id"):
            c_res = await async_execute(
                client.table("contacts")
                .select("name, phone_e164, email")
                .eq("id", conv["contact_id"])
                .limit(1)
            )
            c_rows = getattr(c_res, "data", None) or []
            if c_rows:
                c = c_rows[0]
                contact_label = f"{c.get('name') or c.get('phone_e164') or c.get('email') or '(no contact)'}"

        title = f"Review conversation — {intent} ({urgency})"
        description_lines = [
            f"Reason: {reason}",
            f"Contact: {contact_label or '(unknown)'}",
            f"Channel: {conv.get('channel_type')}",
            f"Intent: {intent}",
            f"Urgency: {urgency}",
        ]
        if summary:
            description_lines.append(f"Summary: {summary}")
        description_lines.append(f"Conversation: {conversation_id}")
        if trace_id:
            description_lines.append(f"Trace: {trace_id}")
        description = "\n".join(description_lines)

        task_row = {
            "business_id": business_id,
            "type": "human_handoff",
            "title": title,
            "description": description,
            "priority": priority,
            "source_table": "conversations",
            "source_id": conversation_id,
            "metadata": {"intent": intent, "urgency": urgency, "trace_id": trace_id},
        }
        res = await async_execute(
            client.table("tasks").insert(task_row)
        )
        task_rows = getattr(res, "data", None) or []
        task = task_rows[0] if task_rows else None

        await update_conversation(
            conversation_id=conversation_id,
            status="awaiting_human",
            metadata_patch={"handoff_task_id": (task or {}).get("id")},
        )

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "conversation.handoff_created",
                "p_payload": {
                    "conversation_id": conversation_id,
                    "business_id": business_id,
                    "task_id": (task or {}).get("id"),
                    "intent": intent,
                    "urgency": urgency,
                    "reason": reason,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "task",
                "p_aggregate_id": (task or {}).get("id"),
                "p_idempotency_key": f"ho:event:{job.id}",
            },
        )

        # Optional operator email.
        business = await fetch_business(business_id) or {}
        escalation = business.get("escalation") or {}
        to_email = escalation.get("email")
        if to_email:
            settings = get_worker_settings()
            try:
                send_email(
                    subject=f"[Revenue Edge] {title}",
                    body=description,
                    to_email=to_email,
                    from_email=settings.default_email_from,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to send operator email: %s", exc)

        return {"task_id": (task or {}).get("id"), "priority": priority, "notified": bool(to_email)}
