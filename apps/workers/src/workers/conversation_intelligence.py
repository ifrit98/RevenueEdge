"""conversation_intelligence — consumes `conversation-intelligence`.

Phase 0: no-op that logs and completes. Phase 2+ will:
  - Load conversation + messages + knowledge_items
  - Call LLM for intent + field extraction + next-best-action
  - Enqueue outbound-actions or human-handoff
"""

from __future__ import annotations

import logging
from typing import Optional

from ..base import BaseWorker, Job

logger = logging.getLogger(__name__)


class ConversationIntelligenceWorker(BaseWorker):
    queue_name = "conversation-intelligence"
    max_concurrency = 2

    async def handle(self, job: Job) -> Optional[dict]:
        logger.info(
            "CI stub: would classify intent",
            extra={
                "trace_id": job.payload.get("trace_id"),
                "event_type": job.payload.get("event_type"),
                "business_id": job.business_id,
            },
        )
        return {"phase": 0, "noop": True}
