"""inbound_normalizer — consumes `inbound-events`.

Phase 0: echoes the event, writes to `public.events`, and emits a
`conversation-intelligence` job as a smoke-test handshake.

Phase 1 responsibilities (per PHASE_1_CHECKLIST.md):
  - Upsert contacts on inbound call/sms
  - Create conversations + messages
  - Classify provider → canonical `channel_type`
  - Emit `conversation-intelligence` job with trace_id propagated
  - Dedupe via `events.idempotency_key`
"""

from __future__ import annotations

import logging
from typing import Optional

from ..base import BaseWorker, Job, PermanentError
from ..supabase_client import rpc

logger = logging.getLogger(__name__)


class InboundNormalizerWorker(BaseWorker):
    queue_name = "inbound-events"
    max_concurrency = 4

    async def handle(self, job: Job) -> Optional[dict]:
        event_type = job.payload.get("event_type")
        trace_id = job.payload.get("trace_id")
        business_id = job.business_id or job.payload.get("business_id")

        if not event_type:
            raise PermanentError("event_type missing from payload")

        logger.info(
            "Normalizing inbound event",
            extra={
                "event_type": event_type,
                "trace_id": trace_id,
                "business_id": business_id,
                "idempotency_key": job.idempotency_key,
            },
        )

        # Phase 0: just persist the event so we can see the loop working.
        # Phase 1 branches by event_type: call.missed, message.received, etc.
        await rpc(
            "enqueue_event",
            {
                "p_event_type": f"inbound.{event_type}",
                "p_payload": job.payload,
                "p_business_id": business_id,
                "p_aggregate_type": "conversation",
                "p_aggregate_id": None,
                "p_idempotency_key": job.idempotency_key,
            },
        )

        # Downstream handoff: ask conversation-intelligence to classify intent.
        downstream_payload = dict(job.payload)
        downstream_payload.setdefault("source", event_type)
        await rpc(
            "enqueue_job",
            {
                "p_queue_name": "conversation-intelligence",
                "p_payload": downstream_payload,
                "p_business_id": business_id,
                "p_idempotency_key": f"ci:{job.id}",
                "p_priority": 20,
            },
        )

        return {"normalized": True, "event_type": event_type, "downstream": "conversation-intelligence"}
