"""Enqueue helper. Calls re-api's internal queue endpoint to enqueue jobs.

Webhooks do not hit Supabase RPCs directly; they go through re-api so
authorization, rate limiting, and audit logging stay centralized.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .settings import get_settings

logger = logging.getLogger(__name__)


async def enqueue_job(
    *,
    queue_name: str,
    payload: dict[str, Any],
    business_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    trace_id: Optional[str] = None,
    priority: int = 100,
    timeout_seconds: float = 5.0,
) -> str:
    s = get_settings()
    headers = {"x-internal-key": s.internal_service_key}
    if trace_id:
        headers["x-trace-id"] = trace_id

    body = {
        "queue_name": queue_name,
        "payload": payload,
        "business_id": business_id,
        "idempotency_key": idempotency_key,
        "priority": priority,
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(
            f"{s.re_api_url.rstrip('/')}/internal/queue/enqueue",
            json=body,
            headers=headers,
        )
        if resp.status_code >= 300:
            logger.error(
                "enqueue_job http %s: %s",
                resp.status_code,
                resp.text[:300],
            )
            resp.raise_for_status()
        return resp.json()["job_id"]
