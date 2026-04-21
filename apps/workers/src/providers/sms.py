"""SMS provider adapter. Retell primary (Retell natively handles Twilio under
the hood via the SMS chat API); Twilio direct as a compliance / fallback path.

Ported from SMB-MetaPattern/apps/api-gateway/app/providers/sms.py.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from time import sleep
from typing import Optional

import httpx

try:
    from twilio.rest import Client as TwilioClient  # type: ignore
except ImportError:  # pragma: no cover
    TwilioClient = None

logger = logging.getLogger(__name__)


@dataclass
class SmsSendResult:
    provider: str
    message_id: str
    to_number: str
    from_number: str
    delivered: bool
    metadata: Optional[dict] = None


async def send_sms_retell(
    *,
    to_number: str,
    from_number: str,
    body: str,
    agent_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> SmsSendResult:
    """Send SMS via Retell's Create SMS Chat endpoint.

    See: https://docs.retellai.com/api-references/create-sms-chat
    """
    api_key = os.getenv("RETELL_API_KEY")

    if not api_key:
        logger.warning("[SMS][dry-run] to=%s from=%s: %s", to_number, from_number, body)
        return SmsSendResult(
            provider="dry_run",
            message_id=f"dryrun-sms-{to_number}",
            to_number=to_number,
            from_number=from_number,
            delivered=False,
        )

    url = "https://api.retellai.com/v2/create-sms-chat"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "from_number": from_number,
        "to_number": to_number,
        "first_message": body,
    }
    if agent_id:
        payload["override_agent_id"] = agent_id
    if metadata:
        payload["metadata"] = metadata

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            data = response.json()
            return SmsSendResult(
                provider="retell",
                message_id=data.get("call_id", f"retell-sms-{to_number}"),
                to_number=to_number,
                from_number=from_number,
                delivered=True,
                metadata=data,
            )
    except httpx.HTTPError as exc:
        logger.error("Retell SMS error: %s", exc)
        return await _send_sms_twilio_fallback(
            to_number=to_number,
            from_number=from_number,
            body=body,
        )


async def _send_sms_twilio_fallback(
    *, to_number: str, from_number: str, body: str
) -> SmsSendResult:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    if account_sid and auth_token and TwilioClient:
        try:
            client = TwilioClient(account_sid, auth_token)
            message = client.messages.create(
                body=body,
                to=to_number,
                from_=from_number,
            )
            return SmsSendResult(
                provider="twilio_fallback",
                message_id=message.sid,
                to_number=to_number,
                from_number=from_number,
                delivered=True,
            )
        except Exception as exc:
            logger.error("Twilio fallback failed: %s", exc)

    logger.warning("[SMS][dry-run] to=%s from=%s: %s", to_number, from_number, body)
    return SmsSendResult(
        provider="dry_run",
        message_id=f"dryrun-sms-{to_number}",
        to_number=to_number,
        from_number=from_number,
        delivered=False,
    )


def send_sms_sync(
    *, to_number: str, from_number: str, body: str, max_retries: int = 2
) -> SmsSendResult:
    """Synchronous SMS via Twilio. Kept for legacy/synchronous call-sites."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    messaging_service_sid = os.getenv("TWILIO_MESSAGING_SERVICE_SID")

    if account_sid and auth_token and TwilioClient:
        client = TwilioClient(account_sid, auth_token)
        attempt = 0
        while True:
            try:
                message = client.messages.create(
                    body=body,
                    to=to_number,
                    from_=from_number if not messaging_service_sid else None,
                    messaging_service_sid=messaging_service_sid,
                )
                return SmsSendResult(
                    provider="twilio",
                    message_id=message.sid,
                    to_number=to_number,
                    from_number=from_number,
                    delivered=True,
                )
            except Exception as exc:
                attempt += 1
                if attempt > max_retries:
                    logger.error("Twilio send failed: %s", exc)
                    break
                sleep(1 * attempt)

    logger.warning("[SMS][dry-run] to=%s from=%s: %s", to_number, from_number, body)
    return SmsSendResult(
        provider="dry_run",
        message_id=f"dryrun-sms-{to_number}",
        to_number=to_number,
        from_number=from_number,
        delivered=False,
    )
