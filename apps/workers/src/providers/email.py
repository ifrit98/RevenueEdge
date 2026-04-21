"""Email provider (SendGrid HTTP). Ported from SMB-MetaPattern."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from time import sleep
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EmailSendResult:
    provider: str
    message_id: str
    to_email: str
    from_email: str
    delivered: bool


SENDGRID_API = "https://api.sendgrid.com/v3/mail/send"


def _send_via_sendgrid(
    *,
    subject: str,
    body: str,
    to_email: str,
    from_email: str,
    max_retries: int = 2,
) -> Optional[str]:
    api_key = os.getenv("SENDGRID_API_KEY")
    if not api_key:
        return None
    payload = {
        "personalizations": [{"to": [{"email": to_email}], "subject": subject}],
        "from": {"email": from_email},
        "content": [{"type": "text/plain", "value": body}],
    }
    request = urllib.request.Request(
        SENDGRID_API,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(request) as response:
                return response.headers.get("X-Message-Id") or "sendgrid"
        except urllib.error.HTTPError as exc:  # pragma: no cover
            attempt += 1
            if attempt > max_retries:
                logger.error("SendGrid error: %s", exc.read())
                break
        except urllib.error.URLError as exc:
            attempt += 1
            if attempt > max_retries:
                logger.error("SendGrid network error: %s", exc)
                break
        sleep(1 * attempt)
    return None


def send_email(
    *, subject: str, body: str, to_email: str, from_email: str
) -> EmailSendResult:
    message_id = _send_via_sendgrid(
        subject=subject, body=body, to_email=to_email, from_email=from_email
    )
    if message_id:
        return EmailSendResult(
            provider="sendgrid",
            message_id=message_id,
            to_email=to_email,
            from_email=from_email,
            delivered=True,
        )

    logger.warning(
        "[Email][dry-run] to=%s from=%s subject=%s", to_email, from_email, subject
    )
    return EmailSendResult(
        provider="dry_run",
        message_id=f"dryrun-email-{to_email}",
        to_email=to_email,
        from_email=from_email,
        delivered=False,
    )
