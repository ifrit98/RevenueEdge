"""Per-contact SMS rate limiting.

Queries the `events` table for recent `outbound.sms.sent` events scoped to a
specific contact. The default cooldown is 120 seconds (configurable via
`businesses.settings.sms_rate_limit_seconds`).

Returns the remaining cooldown in seconds if the limit is hit, or 0 if clear.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..supabase_client import async_execute, get_client

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 120
DEFAULT_DAILY_CAP = 500


async def check_sms_rate_limit(
    *,
    contact_id: str,
    business_id: str,
    cooldown_seconds: Optional[int] = None,
) -> float:
    """Return remaining cooldown seconds before this contact can be SMS'd again.

    Returns 0.0 if the contact is clear to send.
    """
    if not contact_id:
        return 0.0
    cooldown = cooldown_seconds or DEFAULT_COOLDOWN_SECONDS
    client = get_client()
    since = (datetime.now(timezone.utc) - timedelta(seconds=cooldown)).isoformat()
    res = await async_execute(
        client.table("events")
        .select("occurred_at")
        .eq("business_id", business_id)
        .eq("event_type", "outbound.sms.sent")
        .gte("occurred_at", since)
        .order("occurred_at", desc=True)
        .limit(50)
    )
    rows = getattr(res, "data", None) or []
    for row in rows:
        payload = row if isinstance(row, dict) else {}
        if payload.get("occurred_at"):
            return max(0.0, cooldown - (datetime.now(timezone.utc) - _parse_ts(payload["occurred_at"])).total_seconds())
    return 0.0


async def check_daily_cap(
    *,
    business_id: str,
    daily_cap: Optional[int] = None,
) -> bool:
    """Return True if the business has exceeded its daily SMS cap."""
    cap = daily_cap or DEFAULT_DAILY_CAP
    client = get_client()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    res = await async_execute(
        client.table("events")
        .select("id", count="exact")
        .eq("business_id", business_id)
        .eq("event_type", "outbound.sms.sent")
        .gte("occurred_at", today_start)
    )
    count = getattr(res, "count", None) or len(getattr(res, "data", None) or [])
    return int(count) >= cap


def _parse_ts(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)
