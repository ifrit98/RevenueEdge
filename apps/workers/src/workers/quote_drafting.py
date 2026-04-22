"""quote_drafting — consumes `quote-drafting`.

When all required intake fields are collected for a lead, this worker:
  1. Loads the lead, intake_fields, matched service, and business
  2. Renders a draft quote from a template or generates one via LLM
  3. Inserts a ``quotes`` row with ``status = 'awaiting_review'``
  4. Creates a ``quote_review`` task for the operator
  5. Advances the lead to ``awaiting_quote``
  6. Emits ``quote.drafted`` event

Job payload:
  {
    "lead_id": "uuid",
    "conversation_id": "uuid",
    "business_id": "uuid",
    "service_id": "uuid|null",
    "trace_id": "..."
  }
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..base import BaseWorker, Job, PermanentError
from ..lib.channels import fetch_business
from ..lib.leads import advance_lead_stage
from ..lib.llm import classify_conversation
from ..lib.templates import build_render_context, load_template, render_template
from ..supabase_client import async_execute, get_client, rpc

logger = logging.getLogger(__name__)


class QuoteDraftingWorker(BaseWorker):
    queue_name = "quote-drafting"
    max_concurrency = 2

    async def handle(self, job: Job) -> Optional[dict]:
        payload = job.payload or {}
        lead_id = payload.get("lead_id")
        conversation_id = payload.get("conversation_id")
        business_id = job.business_id or payload.get("business_id")
        service_id = payload.get("service_id")
        trace_id = payload.get("trace_id")

        if not lead_id:
            raise PermanentError("lead_id required")
        if not business_id:
            raise PermanentError("business_id required")

        client = get_client()
        business = await fetch_business(business_id)

        lead = await self._load_lead(client, lead_id)
        if not lead:
            raise PermanentError(f"lead {lead_id} not found")

        intake_fields = await self._load_intake_fields(client, lead_id)
        service = await self._load_service(client, service_id) if service_id else None

        if not service and not intake_fields:
            await self._escalate_handoff(
                business_id=business_id,
                conversation_id=conversation_id,
                lead_id=lead_id,
                reason="No service matched and no intake fields collected",
                trace_id=trace_id,
                job_id=job.id,
            )
            return {"action": "handoff", "reason": "no_service_no_fields"}

        draft_text = await self._render_draft(
            business=business,
            service=service,
            intake_fields=intake_fields,
            lead=lead,
        )

        amount_low = (service or {}).get("base_price_low")
        amount_high = (service or {}).get("base_price_high")

        quote_row = {
            "business_id": business_id,
            "lead_id": lead_id,
            "contact_id": lead.get("contact_id"),
            "service_id": service_id,
            "status": "awaiting_review",
            "quote_type": "estimate",
            "amount_low": amount_low,
            "amount_high": amount_high,
            "draft_text": draft_text,
            "metadata": {
                "trace_id": trace_id,
                "intake_field_count": len(intake_fields),
                "service_name": (service or {}).get("name"),
            },
        }
        biz_settings = (business or {}).get("settings") or {}
        auto_max = biz_settings.get("auto_quote_max")
        auto_send = (
            auto_max is not None
            and amount_high is not None
            and float(amount_high) <= float(auto_max)
        )

        quote_row["status"] = "approved" if auto_send else "awaiting_review"
        res = await async_execute(client.table("quotes").insert(quote_row))
        rows = getattr(res, "data", None) or []
        if not rows:
            raise PermanentError("Failed to insert quote row")
        quote = rows[0]

        if auto_send:
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "outbound-actions",
                    "p_payload": {
                        "action": "send_quote",
                        "quote_id": quote["id"],
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "contact_id": lead.get("contact_id"),
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ob:autoquote:{job.id}",
                    "p_priority": 15,
                },
            )
            logger.info(
                "Auto-sending quote %s (amount_high=%s <= auto_max=%s)",
                quote["id"], amount_high, auto_max,
            )
        else:
            await async_execute(
                client.table("tasks").insert({
                    "business_id": business_id,
                    "conversation_id": conversation_id,
                    "task_type": "quote_review",
                    "title": f"Review quote for {(service or {}).get('name', 'service')}",
                    "priority": "high",
                    "status": "open",
                    "metadata": {
                        "quote_id": quote["id"],
                        "lead_id": lead_id,
                        "service_name": (service or {}).get("name"),
                        "amount_range": f"${amount_low or '?'}–${amount_high or '?'}",
                        "trace_id": trace_id,
                    },
                })
            )

        await advance_lead_stage(
            lead_id=lead_id,
            new_stage="proposal",
            metadata_patch={"quote_id": quote["id"], "trace_id": trace_id},
        )

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "quote.drafted",
                "p_payload": {
                    "quote_id": quote["id"],
                    "lead_id": lead_id,
                    "business_id": business_id,
                    "service_id": service_id,
                    "amount_low": amount_low,
                    "amount_high": amount_high,
                    "auto_sent": auto_send,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "quote",
                "p_aggregate_id": quote["id"],
                "p_idempotency_key": f"quote:drafted:{job.id}",
            },
        )

        return {
            "quote_id": quote["id"],
            "status": "approved" if auto_send else "awaiting_review",
            "auto_sent": auto_send,
            "service": (service or {}).get("name"),
            "amount_low": amount_low,
            "amount_high": amount_high,
        }

    async def _render_draft(
        self,
        *,
        business: Optional[dict],
        service: Optional[dict],
        intake_fields: dict[str, str],
        lead: dict,
    ) -> str:
        """Render a human-readable draft quote from template or a simple format."""
        biz_name = (business or {}).get("name") or "Our team"
        svc_name = (service or {}).get("name") or "requested service"
        price_low = (service or {}).get("base_price_low")
        price_high = (service or {}).get("base_price_high")

        lines = [
            f"Quote Estimate — {biz_name}",
            f"Service: {svc_name}",
            "",
        ]
        if intake_fields:
            lines.append("Details provided:")
            for k, v in intake_fields.items():
                lines.append(f"  • {k.replace('_', ' ').title()}: {v}")
            lines.append("")

        if price_low and price_high:
            lines.append(f"Estimated range: ${price_low:,.0f} – ${price_high:,.0f}")
        elif price_low:
            lines.append(f"Starting from: ${price_low:,.0f}")
        else:
            lines.append("Pricing: To be confirmed by our team")

        lines.append("")
        lines.append("This is an estimate. Final pricing may vary based on inspection.")
        return "\n".join(lines)

    async def _escalate_handoff(
        self, *, business_id: str, conversation_id: Optional[str],
        lead_id: str, reason: str, trace_id: Optional[str], job_id: str,
    ) -> None:
        await rpc(
            "enqueue_job",
            {
                "p_queue_name": "human-handoff",
                "p_payload": {
                    "conversation_id": conversation_id,
                    "business_id": business_id,
                    "reason": reason,
                    "lead_id": lead_id,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_idempotency_key": f"ho:quote-esc:{job_id}",
                "p_priority": 5,
            },
        )

    @staticmethod
    async def _load_lead(client: Any, lead_id: str) -> Optional[dict]:
        res = await async_execute(
            client.table("leads").select("*").eq("id", lead_id).limit(1)
        )
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None

    @staticmethod
    async def _load_intake_fields(client: Any, lead_id: str) -> dict[str, str]:
        res = await async_execute(
            client.table("intake_fields")
            .select("field_name, field_value")
            .eq("lead_id", lead_id)
        )
        rows = getattr(res, "data", None) or []
        return {r["field_name"]: r["field_value"] for r in rows if r.get("field_value")}

    @staticmethod
    async def _load_service(client: Any, service_id: str) -> Optional[dict]:
        res = await async_execute(
            client.table("services")
            .select("id, name, description, base_price_low, base_price_high, required_intake_fields, tags, metadata")
            .eq("id", service_id)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
