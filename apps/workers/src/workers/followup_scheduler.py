"""followup_scheduler — consumes `follow-up-scheduler`.

Phase 0: no-op. Phase 4+ will consume scheduled follow-up events and enqueue
appropriate outbound-actions at their `available_at` times.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..base import BaseWorker, Job

logger = logging.getLogger(__name__)


class FollowupSchedulerWorker(BaseWorker):
    queue_name = "follow-up-scheduler"
    max_concurrency = 2

    async def handle(self, job: Job) -> Optional[dict]:
        logger.info(
            "Followup scheduler stub",
            extra={
                "trace_id": job.payload.get("trace_id"),
                "business_id": job.business_id,
                "followup_type": job.payload.get("followup_type"),
            },
        )
        return {"phase": 0, "noop": True}
