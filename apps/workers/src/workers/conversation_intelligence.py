"""conversation_intelligence — consumes `conversation-intelligence`.

Given a conversation_id, load context, ask the LLM (or heuristic fallback)
for intent/urgency/confidence/next-action, persist the decision onto the
conversation row + an event, and dispatch the next step:

  - send_sms_reply          → outbound-actions (ad-hoc body)
  - ask_followup            → outbound-actions (ad-hoc body)
  - collect_quote_details   → outbound-actions w/ template=quote_intake
  - schedule_callback       → outbound-actions w/ template=callback_scheduling
  - handoff                 → human-handoff
  - mark_resolved / noop    → no downstream job
"""

from __future__ import annotations

import logging
from typing import Optional

from ..base import BaseWorker, Job, PermanentError
from ..lib.channels import fetch_business
from ..lib.conversations import load_conversation_context, update_conversation
from ..lib.llm import classify_conversation, coerce_confidence
from ..supabase_client import rpc

logger = logging.getLogger(__name__)


_CONFIDENCE_FLOOR = 0.72


class ConversationIntelligenceWorker(BaseWorker):
    queue_name = "conversation-intelligence"
    max_concurrency = 4

    async def handle(self, job: Job) -> Optional[dict]:
        payload = job.payload or {}
        conversation_id = payload.get("conversation_id")
        business_id = job.business_id or payload.get("business_id")
        trace_id = payload.get("trace_id")
        source_event_type = payload.get("source_event_type")

        if not conversation_id:
            raise PermanentError("conversation_id missing from payload")

        ctx = await load_conversation_context(conversation_id=conversation_id)
        if not ctx:
            raise PermanentError(f"conversation {conversation_id} not found")

        business = await fetch_business(business_id) if business_id else None
        llm_context = {
            "business": business,
            "contact": ctx.get("contact"),
            "conversation": ctx.get("conversation"),
            "messages": ctx.get("messages") or [],
            "source_event_type": source_event_type,
        }

        decision = await classify_conversation(llm_context)
        confidence = coerce_confidence(decision.get("confidence")) or 0.0
        intent = decision.get("intent") or "unknown"
        urgency = decision.get("urgency") or "unknown"
        next_action = decision.get("recommended_next_action") or "handoff"
        reply_text = (decision.get("reply_text") or "").strip()
        summary = (decision.get("summary") or "").strip() or None

        # Low-confidence guardrail: force handoff.
        if confidence < _CONFIDENCE_FLOOR and next_action not in {"handoff", "noop"}:
            logger.info(
                "Confidence below floor — forcing handoff",
                extra={"conversation_id": conversation_id, "confidence": confidence},
            )
            next_action = "handoff"

        await update_conversation(
            conversation_id=conversation_id,
            current_intent=intent,
            urgency=urgency,
            ai_confidence=confidence,
            summary=summary,
            metadata_patch={
                "last_decision": {
                    "next_action": next_action,
                    "model": decision.get("_model"),
                    "trace_id": trace_id,
                    "fields_collected": decision.get("fields_collected") or {},
                }
            },
        )

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "conversation.classified",
                "p_payload": {
                    "conversation_id": conversation_id,
                    "business_id": business_id,
                    "intent": intent,
                    "urgency": urgency,
                    "confidence": confidence,
                    "next_action": next_action,
                    "trace_id": trace_id,
                    "model": decision.get("_model"),
                    "usage": decision.get("_usage"),
                    "summary": summary,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "conversation",
                "p_aggregate_id": conversation_id,
                "p_idempotency_key": f"cls:{job.id}",
            },
        )

        downstream: list[str] = []

        if next_action == "send_sms_reply" and reply_text:
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "outbound-actions",
                    "p_payload": {
                        "action": "send_sms",
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "contact_id": ctx["conversation"].get("contact_id"),
                        "channel_id": ctx["conversation"].get("channel_id"),
                        "body": reply_text,
                        "intent": intent,
                        "trace_id": trace_id,
                        "reason": "ai_reply",
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ob:ai-reply:{job.id}",
                    "p_priority": 20,
                },
            )
            downstream.append("outbound-actions")

        elif next_action in {"collect_quote_details", "ask_followup", "schedule_callback"}:
            template_name = {
                "collect_quote_details": "quote_intake",
                "schedule_callback": "callback_scheduling",
                "ask_followup": None,
            }[next_action]
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "outbound-actions",
                    "p_payload": {
                        "action": "send_sms",
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "contact_id": ctx["conversation"].get("contact_id"),
                        "channel_id": ctx["conversation"].get("channel_id"),
                        "template_name": template_name,
                        "intent": intent,
                        "body": reply_text or None,
                        "trace_id": trace_id,
                        "reason": next_action,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ob:{next_action}:{job.id}",
                    "p_priority": 25,
                },
            )
            downstream.append("outbound-actions")

        elif next_action == "handoff":
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "human-handoff",
                    "p_payload": {
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "reason": decision.get("handoff_reason")
                        or f"AI confidence {confidence} on intent={intent}",
                        "intent": intent,
                        "urgency": urgency,
                        "summary": summary,
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ho:{job.id}",
                    "p_priority": 5,
                },
            )
            downstream.append("human-handoff")

        return {
            "intent": intent,
            "urgency": urgency,
            "confidence": confidence,
            "next_action": next_action,
            "downstream": downstream,
            "model": decision.get("_model"),
        }
