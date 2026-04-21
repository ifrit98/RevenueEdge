"""outbound_action — consumes `outbound-actions`.

Job payload shape (send_sms):
  {
    "action": "send_sms",
    "conversation_id": "<uuid>",
    "business_id": "<uuid>",
    "contact_id": "<uuid>",
    "channel_id": "<uuid or null>",
    "template_name": "<optional>",
    "intent": "<optional>",
    "body": "<optional literal>",
    "trace_id": "...",
    "reason": "..."
  }

Resolution rules:
  - If `body` is provided, send it verbatim (AI-generated reply path).
  - Else resolve `template_name` (preferred) or `intent` → load template → render.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..base import BaseWorker, Job, PermanentError, RetryableError
from ..lib.channels import fetch_business
from ..lib.conversations import insert_message
from ..lib.templates import build_render_context, load_template, render_template
from ..providers.sms import send_sms_retell
from ..settings import get_worker_settings
from ..supabase_client import async_execute, get_client, rpc

logger = logging.getLogger(__name__)


class OutboundActionWorker(BaseWorker):
    queue_name = "outbound-actions"
    max_concurrency = 4

    async def handle(self, job: Job) -> Optional[dict]:
        payload = job.payload or {}
        action = payload.get("action") or "send_sms"
        if action != "send_sms":
            raise PermanentError(f"unsupported action: {action}")

        business_id = job.business_id or payload.get("business_id")
        conversation_id = payload.get("conversation_id")
        contact_id = payload.get("contact_id")
        channel_id = payload.get("channel_id")
        trace_id = payload.get("trace_id")

        if not business_id or not conversation_id:
            raise PermanentError("business_id and conversation_id required")

        client = get_client()
        contact = None
        if contact_id:
            res = await async_execute(
                client.table("contacts")
                .select("id, name, phone_e164, email, metadata")
                .eq("id", contact_id)
                .limit(1)
            )
            rows = getattr(res, "data", None) or []
            contact = rows[0] if rows else None
        conv = None
        res = await async_execute(
            client.table("conversations")
            .select("id, business_id, contact_id, channel_id, channel_type, status, current_intent")
            .eq("id", conversation_id)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        conv = rows[0] if rows else None
        if conv is None:
            raise PermanentError(f"conversation {conversation_id} not found")

        if not contact or not contact.get("phone_e164"):
            raise PermanentError("contact has no phone_e164 — cannot SMS")

        business = await fetch_business(business_id)

        # Respect STOP list (contact.metadata.sms_opt_out=true).
        if (contact.get("metadata") or {}).get("sms_opt_out"):
            logger.info(
                "Contact has opted out — skipping SMS",
                extra={"contact_id": contact_id, "conversation_id": conversation_id},
            )
            await rpc(
                "enqueue_event",
                {
                    "p_event_type": "outbound.sms.skipped_opt_out",
                    "p_payload": {
                        "conversation_id": conversation_id,
                        "contact_id": contact_id,
                        "business_id": business_id,
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_aggregate_type": "conversation",
                    "p_aggregate_id": conversation_id,
                    "p_idempotency_key": f"ob:skip:{job.id}",
                },
            )
            return {"skipped": True, "reason": "sms_opt_out"}

        body = (payload.get("body") or "").strip()
        template_row = None
        if not body:
            template_row = await load_template(
                business_id=business_id,
                name=payload.get("template_name"),
                intent=payload.get("intent"),
                channel_type="sms",
            )
            if not template_row:
                raise PermanentError(
                    f"no SMS template found for name={payload.get('template_name')} intent={payload.get('intent')}"
                )
            ctx = build_render_context(
                business=business,
                contact=contact,
                conversation=conv,
                lead=None,
            )
            body = render_template(template_row["body_template"], ctx)

        body = body.strip()
        if not body:
            raise PermanentError("rendered SMS body is empty")

        settings = get_worker_settings()
        from_number = settings.retell_from_number
        if not from_number:
            # Fall back to the channel's external_id if set.
            if channel_id or conv.get("channel_id"):
                cid = channel_id or conv["channel_id"]
                ch_res = await async_execute(
                    client.table("channels")
                    .select("external_id, config")
                    .eq("id", cid)
                    .limit(1)
                )
                ch_rows = getattr(ch_res, "data", None) or []
                if ch_rows:
                    from_number = ch_rows[0].get("external_id") or (
                        (ch_rows[0].get("config") or {}).get("from_number")
                    )
        if not from_number:
            raise RetryableError("no from_number configured for SMS send")

        try:
            send_result = await send_sms_retell(
                to_number=contact["phone_e164"],
                from_number=from_number,
                body=body,
                metadata={"conversation_id": conversation_id, "trace_id": trace_id},
            )
        except Exception as exc:  # noqa: BLE001
            raise RetryableError(f"SMS provider error: {exc}") from exc

        await insert_message(
            business_id=business_id,
            conversation_id=conversation_id,
            contact_id=contact_id,
            channel_id=channel_id or conv.get("channel_id"),
            direction="outbound",
            sender_type="ai",
            body=body,
            external_message_id=send_result.message_id,
            idempotency_key=f"msg:outbound:{job.id}",
            model_name=template_row["name"] if template_row else None,
            raw_payload={
                "provider": send_result.provider,
                "delivered": send_result.delivered,
                "template_name": (template_row or {}).get("name"),
                "trace_id": trace_id,
            },
        )

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "outbound.sms.sent",
                "p_payload": {
                    "conversation_id": conversation_id,
                    "business_id": business_id,
                    "contact_id": contact_id,
                    "provider": send_result.provider,
                    "delivered": send_result.delivered,
                    "message_id": send_result.message_id,
                    "template_name": (template_row or {}).get("name"),
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "conversation",
                "p_aggregate_id": conversation_id,
                "p_idempotency_key": f"ob:sent:{job.id}",
            },
        )

        return {
            "sent": True,
            "provider": send_result.provider,
            "delivered": send_result.delivered,
            "message_id": send_result.message_id,
            "template_name": (template_row or {}).get("name"),
        }
