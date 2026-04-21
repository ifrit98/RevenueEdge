"""outbound_action — consumes `outbound-actions`.

Phase 0: no-op. Phase 1 will send SMS via providers/sms.py.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..base import BaseWorker, Job

logger = logging.getLogger(__name__)


class OutboundActionWorker(BaseWorker):
    queue_name = "outbound-actions"
    max_concurrency = 4

    async def handle(self, job: Job) -> Optional[dict]:
        action = job.payload.get("action", "sms")
        logger.info(
            "Outbound action stub",
            extra={
                "action": action,
                "trace_id": job.payload.get("trace_id"),
                "business_id": job.business_id,
            },
        )
        return {"phase": 0, "noop": True, "action": action}
