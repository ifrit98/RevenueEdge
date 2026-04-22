"""inbound_normalizer — consumes `inbound-events`.

Responsibilities:
  1. Resolve `business_id` from the inbound DID (channels lookup).
  2. Normalize caller phone to E.164 and upsert `contacts`.
  3. Find or open a conversation on the right channel.
  4. Persist an inbound message row (including voicemail transcript).
  5. Branch by canonical event_type:
       - call.missed       → enqueue outbound-actions (missed_call_recovery)
                             + enqueue conversation-intelligence
       - call.ended        → enqueue conversation-intelligence only
       - message.received  → enqueue conversation-intelligence
       - call.started      → just record the event
  6. Write `public.events` for auditability (idempotency via events.idempotency_key).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..base import BaseWorker, Job, PermanentError
from ..lib.channels import fetch_business, resolve_voice_or_sms_channel
from ..lib.contacts import upsert_contact
from ..lib.conversations import find_or_create_conversation, insert_message
from ..lib.phone import normalize_phone
from ..supabase_client import rpc

logger = logging.getLogger(__name__)


_EVENT_CHANNEL_HINT = {
    "call.missed": "phone",
    "call.ended": "phone",
    "call.started": "phone",
    "message.received": "sms",
}

_STOP_KEYWORDS = {"stop", "unsubscribe", "cancel", "quit", "end"}
_START_KEYWORDS = {"start", "unstop", "subscribe", "resume", "yes"}
_HELP_KEYWORDS = {"help", "info"}


class InboundNormalizerWorker(BaseWorker):
    queue_name = "inbound-events"
    max_concurrency = 4

    async def handle(self, job: Job) -> Optional[dict]:
        payload = job.payload or {}
        event_type = payload.get("event_type")
        trace_id = payload.get("trace_id")

        if not event_type:
            raise PermanentError("event_type missing from payload")

        channel_type_hint = _EVENT_CHANNEL_HINT.get(event_type)
        to_number = normalize_phone(payload.get("to_number"))
        from_number = normalize_phone(payload.get("from_number"))

        # Resolve channel + business_id.
        provider = payload.get("source") or "retell"
        channel_row, channel_type = await resolve_voice_or_sms_channel(
            provider=provider,
            external_id=payload.get("to_number") or to_number,
            channel_type_hint=channel_type_hint,
        )
        business_id = (channel_row or {}).get("business_id") or job.business_id or payload.get("business_id")

        if not business_id:
            # Record an events row anyway for debugging + dead-letter.
            await rpc(
                "enqueue_event",
                {
                    "p_event_type": f"inbound.{event_type}.unrouted",
                    "p_payload": payload,
                    "p_business_id": None,
                    "p_aggregate_type": "channel",
                    "p_aggregate_id": None,
                    "p_idempotency_key": job.idempotency_key,
                },
            )
            raise PermanentError(
                f"No business_id resolvable for provider={provider} to_number={payload.get('to_number')}"
            )

        business = await fetch_business(business_id)

        # Upsert contact on the caller's from_number.
        contact = None
        if from_number:
            contact = await upsert_contact(
                business_id=business_id,
                phone_e164=from_number,
                name=None,
                source_channel=channel_type,
            )

        conversation = None
        if contact:
            conversation = await find_or_create_conversation(
                business_id=business_id,
                contact_id=contact["id"],
                channel_id=(channel_row or {}).get("id"),
                channel_type=channel_type or channel_type_hint or "phone",
                initial_status="open",
                metadata={"source_event": event_type, "trace_id": trace_id},
            )

        # Persist an inbound message row when we have a conversation + body-ish content.
        body = self._extract_body(payload, event_type)
        external_message_id = payload.get("call_id") or payload.get("message_id")
        if conversation and (body or event_type in {"call.missed", "call.ended", "call.started"}):
            await insert_message(
                business_id=business_id,
                conversation_id=conversation["id"],
                contact_id=(contact or {}).get("id"),
                channel_id=(channel_row or {}).get("id"),
                direction="inbound",
                sender_type="customer",
                body=body,
                external_message_id=external_message_id,
                idempotency_key=f"msg:inbound:{event_type}:{external_message_id}" if external_message_id else None,
                raw_payload=payload,
            )

        # STOP / START / HELP compliance (SMS only).
        if event_type == "message.received" and body and contact:
            sms_command = self._detect_sms_command(body)
            if sms_command:
                await self._handle_sms_command(
                    sms_command, contact=contact, business_id=business_id,
                    conversation=conversation, trace_id=trace_id, job_id=job.id,
                )
                if sms_command in ("stop", "help"):
                    return {
                        "normalized": True,
                        "event_type": event_type,
                        "sms_command": sms_command,
                        "business_id": business_id,
                        "contact_id": contact.get("id"),
                        "downstream": [],
                    }

        # Audit event.
        await rpc(
            "enqueue_event",
            {
                "p_event_type": f"inbound.{event_type}",
                "p_payload": {
                    **payload,
                    "business_id": business_id,
                    "conversation_id": (conversation or {}).get("id"),
                    "contact_id": (contact or {}).get("id"),
                    "channel_id": (channel_row or {}).get("id"),
                },
                "p_business_id": business_id,
                "p_aggregate_type": "conversation",
                "p_aggregate_id": (conversation or {}).get("id"),
                "p_idempotency_key": job.idempotency_key,
            },
        )

        downstream: list[str] = []
        ci_payload = {
            "trace_id": trace_id,
            "source_event_type": event_type,
            "business_id": business_id,
            "conversation_id": (conversation or {}).get("id"),
            "contact_id": (contact or {}).get("id"),
            "channel_id": (channel_row or {}).get("id"),
        }

        # Missed-call recovery: fire outbound textback immediately (autopilot-safe).
        if event_type == "call.missed" and conversation and contact and contact.get("phone_e164"):
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "outbound-actions",
                    "p_payload": {
                        **ci_payload,
                        "action": "send_sms",
                        "template_name": "missed_call_recovery",
                        "intent": "missed_call",
                        "reason": "call.missed",
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ob:missed:{conversation['id']}:{external_message_id or trace_id}",
                    "p_priority": 10,
                },
            )
            downstream.append("outbound-actions")

        # Always classify so the dashboard has up-to-date intent + urgency.
        if event_type != "call.started":
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "conversation-intelligence",
                    "p_payload": ci_payload,
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ci:{job.id}",
                    "p_priority": 20,
                },
            )
            downstream.append("conversation-intelligence")

        logger.info(
            "Inbound event normalized",
            extra={
                "event_type": event_type,
                "business_id": business_id,
                "conversation_id": (conversation or {}).get("id"),
                "contact_id": (contact or {}).get("id"),
                "trace_id": trace_id,
                "downstream": downstream,
            },
        )

        return {
            "normalized": True,
            "event_type": event_type,
            "business_id": business_id,
            "conversation_id": (conversation or {}).get("id"),
            "contact_id": (contact or {}).get("id"),
            "downstream": downstream,
            "has_business_hours": bool((business or {}).get("hours")),
        }

    @staticmethod
    def _extract_body(payload: dict, event_type: str) -> Optional[str]:
        transcript = payload.get("transcript")
        if transcript:
            return transcript
        text = payload.get("text") or payload.get("message") or payload.get("body")
        if text:
            return text
        if event_type == "call.missed":
            reason = payload.get("disconnection_reason") or "missed"
            return f"[missed call — disconnection: {reason}]"
        if event_type == "call.ended":
            return None
        return None

    @staticmethod
    def _detect_sms_command(body: str) -> Optional[str]:
        """Return ``'stop'``, ``'start'``, ``'help'``, or None."""
        token = body.strip().lower()
        if token in _STOP_KEYWORDS:
            return "stop"
        if token in _START_KEYWORDS:
            return "start"
        if token in _HELP_KEYWORDS:
            return "help"
        return None

    @staticmethod
    async def _handle_sms_command(
        command: str,
        *,
        contact: dict,
        business_id: str,
        conversation: Optional[dict],
        trace_id: Optional[str],
        job_id: str,
    ) -> None:
        from ..supabase_client import async_execute, get_client

        client = get_client()
        contact_id = contact["id"]

        if command == "stop":
            existing_meta = contact.get("metadata") or {}
            existing_meta["sms_opt_out"] = True
            existing_meta["sms_opt_out_at"] = datetime.now(timezone.utc).isoformat()
            await async_execute(
                client.table("contacts")
                .update({"metadata": existing_meta})
                .eq("id", contact_id)
            )
            logger.info("Contact opted out of SMS", extra={"contact_id": contact_id})
            await rpc(
                "enqueue_event",
                {
                    "p_event_type": "contact.sms_opt_out",
                    "p_payload": {
                        "contact_id": contact_id,
                        "business_id": business_id,
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_aggregate_type": "contact",
                    "p_aggregate_id": contact_id,
                    "p_idempotency_key": f"optout:{job_id}",
                },
            )

        elif command == "start":
            existing_meta = contact.get("metadata") or {}
            existing_meta.pop("sms_opt_out", None)
            existing_meta["sms_opt_in_at"] = datetime.now(timezone.utc).isoformat()
            await async_execute(
                client.table("contacts")
                .update({"metadata": existing_meta})
                .eq("id", contact_id)
            )
            logger.info("Contact opted back into SMS", extra={"contact_id": contact_id})
            await rpc(
                "enqueue_event",
                {
                    "p_event_type": "contact.sms_opt_in",
                    "p_payload": {
                        "contact_id": contact_id,
                        "business_id": business_id,
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_aggregate_type": "contact",
                    "p_aggregate_id": contact_id,
                    "p_idempotency_key": f"optin:{job_id}",
                },
            )

        elif command == "help":
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "outbound-actions",
                    "p_payload": {
                        "action": "send_sms",
                        "conversation_id": (conversation or {}).get("id"),
                        "business_id": business_id,
                        "contact_id": contact_id,
                        "body": (
                            "Reply STOP to opt out. Reply START to opt back in. "
                            "For assistance, contact us directly. Msg & data rates may apply."
                        ),
                        "trace_id": trace_id,
                        "reason": "help_response",
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"help:{job_id}",
                    "p_priority": 5,
                },
            )
