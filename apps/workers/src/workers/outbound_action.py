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
from datetime import datetime, timezone
from typing import Optional

from ..base import BaseWorker, Job, PermanentError, RetryableError
from ..lib.channels import fetch_business
from ..lib.conversations import insert_message
from ..lib.hours import is_quiet_hours, next_business_open
from ..lib.rate_limit import check_daily_cap, check_sms_rate_limit
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
        if action == "send_quote":
            return await self._handle_send_quote(job, payload)
        if action == "request_photo":
            return await self._handle_request_photo(job, payload)
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

        biz_settings = business.get("settings") or {} if business else {}
        cooldown = biz_settings.get("sms_rate_limit_seconds")
        remaining = await check_sms_rate_limit(
            contact_id=contact_id,
            business_id=business_id,
            cooldown_seconds=cooldown,
        )
        if remaining > 0:
            logger.info(
                "Rate limit active — deferring SMS by %.0fs",
                remaining,
                extra={"contact_id": contact_id, "remaining": remaining},
            )
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "outbound-actions",
                    "p_payload": payload,
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ob:defer:{job.id}",
                    "p_priority": payload.get("priority", 50),
                    "p_available_at": f"now() + interval '{int(remaining)} seconds'",
                },
            )
            return {"deferred": True, "reason": "rate_limit", "deferred_seconds": remaining}

        daily_cap = biz_settings.get("sms_daily_cap")
        if await check_daily_cap(business_id=business_id, daily_cap=daily_cap):
            logger.warning(
                "Daily SMS cap reached",
                extra={"business_id": business_id},
            )
            return {"skipped": True, "reason": "daily_cap_reached"}

        is_emergency = payload.get("urgency") == "emergency"
        is_autopilot_first = payload.get("reason") in {
            "call.missed", "after_hours_intake"
        }

        if is_quiet_hours(business) and not is_emergency and not is_autopilot_first:
            open_at = next_business_open(business)
            if open_at:
                logger.info(
                    "Quiet hours — deferring SMS until %s", open_at.isoformat(),
                    extra={"business_id": business_id},
                )
                await rpc(
                    "enqueue_job",
                    {
                        "p_queue_name": "outbound-actions",
                        "p_payload": payload,
                        "p_business_id": business_id,
                        "p_idempotency_key": f"ob:quiet:{job.id}",
                        "p_priority": payload.get("priority", 50),
                        "p_available_at": open_at.isoformat(),
                    },
                )
                return {"deferred": True, "reason": "quiet_hours", "deferred_until": open_at.isoformat()}
            return {"skipped": True, "reason": "quiet_hours_no_open_at"}

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

    async def _handle_send_quote(self, job: Job, payload: dict) -> dict:
        """Send an approved quote to the customer via SMS."""
        quote_id = payload.get("quote_id")
        business_id = payload.get("business_id")
        contact_id = payload.get("contact_id")
        conversation_id = payload.get("conversation_id")
        lead_id = payload.get("lead_id")
        trace_id = payload.get("trace_id")

        if not quote_id or not business_id:
            raise PermanentError("quote_id and business_id required for send_quote")

        client = get_client()

        res = await async_execute(
            client.table("quotes")
            .select("id, draft_text, amount_low, amount_high, status")
            .eq("id", quote_id)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            raise PermanentError(f"quote {quote_id} not found")
        quote = rows[0]

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

        if not contact or not contact.get("phone_e164"):
            raise PermanentError("contact has no phone_e164 for quote send")

        if (contact.get("metadata") or {}).get("sms_opt_out"):
            return {"skipped": True, "reason": "sms_opt_out"}

        business = await fetch_business(business_id)
        biz_name = (business or {}).get("name") or "Our team"
        draft = quote.get("draft_text") or ""
        body = draft[:300] if len(draft) <= 320 else draft[:300] + "… Reply for full details."
        if not body:
            body = f"Hi! {biz_name} has prepared a quote for you. Reply for details or call us to discuss."

        settings = get_worker_settings()
        from_number = settings.retell_from_number
        if not from_number:
            raise RetryableError("no from_number configured for SMS send")

        try:
            send_result = await send_sms_retell(
                to_number=contact["phone_e164"],
                from_number=from_number,
                body=body,
                metadata={"quote_id": quote_id, "conversation_id": conversation_id, "trace_id": trace_id},
            )
        except Exception as exc:
            raise RetryableError(f"SMS provider error: {exc}") from exc

        now = datetime.now(timezone.utc).isoformat()
        await async_execute(
            client.table("quotes")
            .update({"status": "sent", "sent_at": now, "updated_at": now})
            .eq("id", quote_id)
        )

        if conversation_id:
            await insert_message(
                business_id=business_id,
                conversation_id=conversation_id,
                contact_id=contact_id,
                channel_id=None,
                direction="outbound",
                sender_type="ai",
                body=body,
                external_message_id=send_result.message_id,
                idempotency_key=f"msg:quote:{job.id}",
                raw_payload={"quote_id": quote_id, "provider": send_result.provider, "trace_id": trace_id},
            )

        if lead_id:
            from ..lib.leads import advance_lead_stage
            await advance_lead_stage(lead_id=lead_id, new_stage="proposal")

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "quote.sent",
                "p_payload": {
                    "quote_id": quote_id,
                    "business_id": business_id,
                    "contact_id": contact_id,
                    "lead_id": lead_id,
                    "provider": send_result.provider,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "quote",
                "p_aggregate_id": quote_id,
                "p_idempotency_key": f"ob:quote-sent:{job.id}",
            },
        )

        if lead_id:
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "follow-up-scheduler",
                    "p_payload": {
                        "followup_type": "quote_recovery",
                        "lead_id": lead_id,
                        "quote_id": quote_id,
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "attempt": 1,
                        "max_attempts": 3,
                        "delays_days": [2, 4, 7],
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"fu:qr:{quote_id}",
                    "p_priority": 40,
                    "p_available_at": f"now() + interval '2 days'",
                },
            )

        return {
            "sent": True,
            "quote_id": quote_id,
            "provider": send_result.provider,
        }

    async def _handle_request_photo(self, job: Job, payload: dict) -> dict:
        """Send the customer an SMS with a photo upload link."""
        business_id = payload.get("business_id")
        conversation_id = payload.get("conversation_id")
        contact_id = payload.get("contact_id")
        trace_id = payload.get("trace_id")
        purpose = payload.get("purpose") or "photo_request"

        if not business_id or not conversation_id:
            raise PermanentError("business_id and conversation_id required for request_photo")

        client = get_client()
        contact = None
        if contact_id:
            res = await async_execute(
                client.table("contacts")
                .select("id, name, phone_e164, metadata")
                .eq("id", contact_id)
                .limit(1)
            )
            rows = getattr(res, "data", None) or []
            contact = rows[0] if rows else None

        if not contact or not contact.get("phone_e164"):
            raise PermanentError("contact has no phone for photo request SMS")

        if (contact.get("metadata") or {}).get("sms_opt_out"):
            return {"skipped": True, "reason": "sms_opt_out"}

        settings = get_worker_settings()
        api_url = settings.re_api_url.rstrip("/")
        internal_key = settings.internal_service_key

        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                f"{api_url}/v1/uploads/request-link",
                json={
                    "conversation_id": conversation_id,
                    "contact_id": contact_id,
                    "purpose": purpose,
                },
                headers={
                    "x-internal-key": internal_key,
                    "x-business-id": business_id,
                    "x-user-id": "system",
                },
            )
            if resp.status_code >= 300:
                raise RetryableError(f"Upload link request failed: {resp.status_code} {resp.text[:200]}")
            link_data = resp.json()

        upload_url = link_data.get("upload_url", "")
        business = await fetch_business(business_id)
        biz_name = (business or {}).get("name") or "Our team"
        body = (
            f"Hi! {biz_name} here. Could you send us a photo of the area or "
            f"issue? You can upload it here: {upload_url}\n\n"
            f"Or simply reply to this message with a picture if your phone supports it."
        )

        from_number = settings.retell_from_number
        if not from_number:
            raise RetryableError("no from_number configured for SMS send")

        try:
            send_result = await send_sms_retell(
                to_number=contact["phone_e164"],
                from_number=from_number,
                body=body,
                metadata={"conversation_id": conversation_id, "trace_id": trace_id, "purpose": purpose},
            )
        except Exception as exc:
            raise RetryableError(f"SMS provider error: {exc}") from exc

        await insert_message(
            business_id=business_id,
            conversation_id=conversation_id,
            contact_id=contact_id,
            channel_id=None,
            direction="outbound",
            sender_type="ai",
            body=body,
            external_message_id=send_result.message_id,
            idempotency_key=f"msg:photo-req:{job.id}",
            raw_payload={"upload_url": upload_url, "trace_id": trace_id, "purpose": purpose},
        )

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "outbound.photo_request.sent",
                "p_payload": {
                    "conversation_id": conversation_id,
                    "business_id": business_id,
                    "contact_id": contact_id,
                    "upload_url": upload_url,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "conversation",
                "p_aggregate_id": conversation_id,
                "p_idempotency_key": f"ob:photo-req:{job.id}",
            },
        )

        return {"sent": True, "upload_url": upload_url, "provider": send_result.provider}
