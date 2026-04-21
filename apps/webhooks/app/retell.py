"""Retell webhook router.

MVP scope:
  - Accept Retell voice + SMS events (Retell natively handles Twilio under
    the hood for SMS).
  - Verify signature with `retell.lib.verify`.
  - Emit an `inbound-events` job per the pack's payload contract.
  - Return 2xx < 500 ms. No heavy work in-handler.

Ported and aggressively trimmed from
SMB-MetaPattern/apps/api-gateway/app/webhooks_retell.py (1170 L) and
SMB-MetaPattern/apps/api-gateway/app/retell_inbound.py (212 L). Dropped:
  - Pipeline v2 integration, CMA chat injection, per-agent routing
  - Real-estate specific dynamic-variable population
  - Heavy synchronous post-call processing
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from retell.lib import verify as retell_verify
except ImportError:  # pragma: no cover - optional dep
    retell_verify = None  # type: ignore

from .enqueue import enqueue_job
from .settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/retell", tags=["webhooks"])


class RetellCall(BaseModel):
    call_id: Optional[str] = None
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    call_status: Optional[str] = None
    call_type: Optional[str] = None
    direction: Optional[str] = None
    disconnection_reason: Optional[str] = None
    start_timestamp: Optional[int] = None
    end_timestamp: Optional[int] = None
    duration_ms: Optional[int] = None
    transcript: Optional[str] = None
    transcript_object: Optional[Any] = None
    recording_url: Optional[str] = None
    collected_dynamic_variables: Optional[dict[str, Any]] = None
    call_analysis: Optional[dict[str, Any]] = None
    agent_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class RetellEvent(BaseModel):
    event: str = Field(...)
    call: Optional[RetellCall] = None


def _verify_signature(raw_body: bytes, signature: Optional[str]) -> None:
    s = get_settings()
    secret = s.retell_webhook_secret or s.retell_api_key
    if s.environment == "test":
        return
    if not signature:
        raise HTTPException(status_code=401, detail="Missing x-retell-signature")
    if retell_verify is None:
        raise HTTPException(status_code=500, detail="retell SDK not installed")
    if not secret:
        raise HTTPException(status_code=500, detail="Retell secret not configured")
    if not retell_verify(raw_body.decode("utf-8"), secret, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")


def _canonical_event_type(retell_event: str, call: Optional[RetellCall]) -> Optional[str]:
    """Map Retell event → canonical event_type used by our queue.

    Returns None for events we ignore at the webhook layer.
    """
    event = (retell_event or "").lower()
    call_type = (call.call_type if call else None) or ""
    is_sms = call_type.lower() == "chat" or (call.direction == "chat" if call else False)
    is_missed = False
    if call and call.disconnection_reason:
        reason = call.disconnection_reason.lower()
        is_missed = any(k in reason for k in ("voicemail", "not_connected", "no_answer", "user_hangup_before_connect"))

    if event in {"call_started", "call_inbound"} and not is_sms:
        return "call.started"
    if event in {"call_ended", "call_analyzed"}:
        if is_sms:
            return "message.received"
        if is_missed or (call and call.duration_ms is not None and call.duration_ms < 3000):
            return "call.missed"
        return "call.ended"
    if event in {"call_message", "chat_message_created"}:
        return "message.received"
    return None


@router.post("")
async def handle_retell_webhook(request: Request) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("x-retell-signature")
    _verify_signature(raw_body, signature)

    try:
        payload_dict = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    try:
        payload = RetellEvent.model_validate(payload_dict)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Schema error: {exc}")

    canonical = _canonical_event_type(payload.event, payload.call)
    if not canonical:
        logger.info("Ignoring Retell event: %s", payload.event)
        return {"ok": True, "ignored": True, "event": payload.event}

    call = payload.call or RetellCall()
    trace_id = (
        (call.metadata or {}).get("trace_id")
        if call.metadata
        else None
    ) or str(uuid.uuid4())

    idempotency_key = None
    if call.call_id:
        idempotency_key = f"inbound:retell:{call.call_id}:{canonical}"

    job_payload: dict[str, Any] = {
        "event_type": canonical,
        "trace_id": trace_id,
        "source": "retell",
        "retell_event": payload.event,
        "call_id": call.call_id,
        "from_number": call.from_number,
        "to_number": call.to_number,
        "call_status": call.call_status,
        "direction": call.direction,
        "disconnection_reason": call.disconnection_reason,
        "start_timestamp": call.start_timestamp,
        "end_timestamp": call.end_timestamp,
        "duration_ms": call.duration_ms,
        "transcript": call.transcript,
        "recording_url": call.recording_url,
        "collected_dynamic_variables": call.collected_dynamic_variables,
        "call_analysis": call.call_analysis,
        "agent_id": call.agent_id,
        "raw_metadata": call.metadata,
    }

    # business_id routing happens downstream in inbound_normalizer (lookup
    # by `to_number` in `channels`). Webhook doesn't block on DB to keep
    # latency low.
    try:
        job_id = await enqueue_job(
            queue_name="inbound-events",
            payload=job_payload,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            priority=10,
        )
    except Exception as exc:
        logger.exception("Failed to enqueue inbound-events job: %s", exc)
        # Still return 2xx so Retell doesn't retry floods; rely on Retell
        # event replay + Sentry alert for recovery.
        return {"ok": False, "error": "enqueue_failed", "trace_id": trace_id}

    logger.info(
        "Retell webhook routed",
        extra={
            "retell_event": payload.event,
            "canonical": canonical,
            "trace_id": trace_id,
            "job_id": job_id,
            "call_id": call.call_id,
        },
    )
    return {"ok": True, "job_id": job_id, "trace_id": trace_id, "event_type": canonical}
