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

Phase 2 additions:
  - After-hours branch: auto after-hours intake + deferred follow-up
  - Knowledge injection: retrieve relevant KB articles for LLM context
  - Leads bridge: find_or_create_lead, advance stage on classification
  - Knowledge-missing fallback: create a review task if KB has no match
"""

from __future__ import annotations

import logging
from typing import Optional

from ..base import BaseWorker, Job, PermanentError
from ..lib.channels import fetch_business
from ..lib.conversations import load_conversation_context, update_conversation
from ..lib.hours import is_within_business_hours, next_business_open
from ..lib.knowledge import retrieve_knowledge
from ..lib.leads import (
    advance_lead_stage,
    check_required_fields_complete,
    find_or_create_lead,
    upsert_intake_fields,
)
from ..lib.llm import classify_conversation, coerce_confidence
from ..supabase_client import async_execute, get_client, rpc

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
        contact = ctx.get("contact")
        messages = ctx.get("messages") or []

        # --- Phase 2: Knowledge injection ---------------------------------
        kb_articles: list[dict] = []
        latest_body = ""
        for m in reversed(messages):
            if m.get("direction") == "inbound" and m.get("body"):
                latest_body = m["body"]
                break
        if latest_body and business_id:
            try:
                kb_articles = await retrieve_knowledge(
                    business_id=business_id,
                    query=latest_body,
                    limit=3,
                )
            except Exception:
                logger.warning("Knowledge retrieval failed", exc_info=True)

        # --- Phase 3: Load active services for multi-turn intake ----------
        services: list[dict] = []
        if business_id:
            try:
                client = get_client()
                svc_res = await async_execute(
                    client.table("services")
                    .select("id, name, description, base_price_low, base_price_high, required_intake_fields, tags")
                    .eq("business_id", business_id)
                    .eq("active", True)
                    .limit(20)
                )
                services = getattr(svc_res, "data", None) or []
            except Exception:
                logger.warning("Services load failed", exc_info=True)

        llm_context = {
            "business": business,
            "contact": contact,
            "conversation": ctx.get("conversation"),
            "messages": messages,
            "source_event_type": source_event_type,
            "knowledge_articles": kb_articles,
            "services": services,
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

        # --- Phase 2: After-hours branch ----------------------------------
        after_hours = False
        if business and not is_within_business_hours(business):
            after_hours = True
            if intent != "emergency" and next_action not in {"handoff", "noop", "mark_resolved"}:
                next_action = "send_sms_reply"
                reply_text = (
                    decision.get("after_hours_reply")
                    or f"Thanks for reaching out! We're currently outside of business hours "
                    f"but we received your message and will follow up first thing. "
                    f"If this is urgent, please call back during business hours."
                )

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
                    "after_hours": after_hours,
                    "kb_articles_used": len(kb_articles),
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
                    "after_hours": after_hours,
                    "kb_articles_used": len(kb_articles),
                },
                "p_business_id": business_id,
                "p_aggregate_type": "conversation",
                "p_aggregate_id": conversation_id,
                "p_idempotency_key": f"cls:{job.id}",
            },
        )

        # --- Leads bridge + Phase 3 intake fields -------------------------
        lead: dict | None = None
        if contact and business_id and intent not in {"unknown", "spam"}:
            try:
                lead = await find_or_create_lead(
                    business_id=business_id,
                    contact_id=contact["id"],
                    conversation_id=conversation_id,
                    source=source_event_type or "inbound",
                    initial_intent=intent,
                    trace_id=trace_id,
                )
                stage_map = {
                    "send_sms_reply": "contacted",
                    "ask_followup": "contacted",
                    "ask_question": "contacted",
                    "collect_quote_details": "qualified",
                    "schedule_callback": "qualified",
                    "draft_quote": "qualified",
                    "handoff": None,
                }
                target_stage = stage_map.get(next_action)
                if target_stage and lead.get("stage") in {"new", "contacted"}:
                    await advance_lead_stage(
                        lead_id=lead["id"],
                        new_stage=target_stage,
                        metadata_patch={"last_intent": intent, "trace_id": trace_id},
                    )

                fields_collected = decision.get("fields_collected") or {}
                if fields_collected and lead:
                    last_msg_id = None
                    for m in reversed(messages):
                        if m.get("direction") == "inbound":
                            last_msg_id = m.get("id")
                            break
                    await upsert_intake_fields(
                        lead_id=lead["id"],
                        fields=fields_collected,
                        source_message_id=last_msg_id,
                    )

                    service_id = decision.get("service_id") or lead.get("service_id")
                    if service_id:
                        complete, missing = await check_required_fields_complete(
                            lead_id=lead["id"], service_id=service_id,
                        )
                        if complete and next_action in {"ask_question", "collect_quote_details"}:
                            next_action = "draft_quote"

            except Exception:
                logger.warning("Leads bridge failed", exc_info=True)

        # --- Phase 2: Knowledge-missing fallback --------------------------
        if not kb_articles and intent in {"faq", "objection", "product_question"} and business_id:
            try:
                client = get_client()
                await async_execute(
                    client.table("tasks").insert({
                        "business_id": business_id,
                        "conversation_id": conversation_id,
                        "task_type": "knowledge_gap",
                        "title": f"Knowledge gap: {intent} — {(latest_body or '')[:80]}",
                        "priority": "medium",
                        "status": "open",
                        "metadata": {
                            "intent": intent,
                            "query_text": latest_body[:200] if latest_body else None,
                            "trace_id": trace_id,
                        },
                    })
                )
            except Exception:
                logger.warning("Failed to create knowledge-gap task", exc_info=True)

        # --- Dispatch downstream ------------------------------------------
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
                        "reason": "after_hours_intake" if after_hours else "ai_reply",
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ob:ai-reply:{job.id}",
                    "p_priority": 20,
                },
            )
            downstream.append("outbound-actions")

            # After-hours: also schedule a follow-up at next business open.
            if after_hours:
                open_at = next_business_open(business)
                if open_at:
                    await rpc(
                        "enqueue_job",
                        {
                            "p_queue_name": "follow-up-scheduler",
                            "p_payload": {
                                "conversation_id": conversation_id,
                                "business_id": business_id,
                                "followup_type": "after_hours_review",
                                "attempt": 1,
                                "max_attempts": 1,
                                "trace_id": trace_id,
                            },
                            "p_business_id": business_id,
                            "p_idempotency_key": f"fu:after:{job.id}",
                            "p_priority": 30,
                            "p_available_at": open_at.isoformat(),
                        },
                    )
                    downstream.append("follow-up-scheduler")
                    await update_conversation(
                        conversation_id=conversation_id,
                        status="awaiting_customer",
                    )

        elif next_action == "ask_question" and reply_text:
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
                        "reason": "intake_question",
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ob:ask-q:{job.id}",
                    "p_priority": 20,
                },
            )
            downstream.append("outbound-actions")

        elif next_action == "draft_quote" and lead:
            service_id = decision.get("service_id") or (lead or {}).get("service_id")
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "quote-drafting",
                    "p_payload": {
                        "lead_id": lead["id"],
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "service_id": service_id,
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"qd:{job.id}",
                    "p_priority": 15,
                },
            )
            downstream.append("quote-drafting")

        elif next_action in {"book", "confirm_booking"}:
            service_id = decision.get("service_id") or (lead or {}).get("service_id")
            preferred_time = decision.get("preferred_time")
            booking_confirmed = next_action == "confirm_booking" or decision.get("booking_confirmed", False)
            biz_settings = (business or {}).get("settings") or {}
            if biz_settings.get("booking_automation_enabled"):
                await rpc(
                    "enqueue_job",
                    {
                        "p_queue_name": "booking-sync",
                        "p_payload": {
                            "lead_id": (lead or {}).get("id"),
                            "conversation_id": conversation_id,
                            "business_id": business_id,
                            "contact_id": ctx["conversation"].get("contact_id"),
                            "service_id": service_id,
                            "preferred_time": preferred_time,
                            "confirmed": booking_confirmed,
                            "trace_id": trace_id,
                        },
                        "p_business_id": business_id,
                        "p_idempotency_key": f"bk:{job.id}",
                        "p_priority": 10,
                    },
                )
                downstream.append("booking-sync")
            else:
                await rpc(
                    "enqueue_job",
                    {
                        "p_queue_name": "human-handoff",
                        "p_payload": {
                            "conversation_id": conversation_id,
                            "business_id": business_id,
                            "reason": "booking_requested_no_automation",
                            "task_type": "callback",
                            "trace_id": trace_id,
                        },
                        "p_business_id": business_id,
                        "p_idempotency_key": f"ho:book:{job.id}",
                        "p_priority": 15,
                    },
                )
                downstream.append("human-handoff")

        elif next_action == "request_photo":
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "outbound-actions",
                    "p_payload": {
                        "action": "request_photo",
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "contact_id": ctx["conversation"].get("contact_id"),
                        "purpose": decision.get("photo_purpose") or "visual_assessment",
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ob:photo:{job.id}",
                    "p_priority": 15,
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
            "after_hours": after_hours,
            "kb_articles_used": len(kb_articles),
            "downstream": downstream,
            "model": decision.get("_model"),
        }
