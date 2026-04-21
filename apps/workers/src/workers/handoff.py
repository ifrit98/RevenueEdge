"""handoff — consumes `human-handoff`.

Phase 0: no-op. Phase 1 creates `tasks` rows and optionally emails operators.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..base import BaseWorker, Job

logger = logging.getLogger(__name__)


class HandoffWorker(BaseWorker):
    queue_name = "human-handoff"
    max_concurrency = 2

    async def handle(self, job: Job) -> Optional[dict]:
        logger.info(
            "Handoff stub",
            extra={
                "trace_id": job.payload.get("trace_id"),
                "business_id": job.business_id,
                "reason": job.payload.get("handoff_reason"),
            },
        )
        return {"phase": 0, "noop": True}
