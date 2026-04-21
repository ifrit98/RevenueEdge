"""Channel + business resolution helpers.

The webhook layer only knows `to_number` / `from_number` / provider; the
`business_id` is derived here by consulting `public.channels`.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from ..supabase_client import async_execute, get_client

logger = logging.getLogger(__name__)


async def resolve_channel_by_external_id(
    *,
    provider: str,
    external_id: str,
) -> Optional[dict]:
    """Find an active `channels` row by provider + external_id.

    `external_id` for Retell is the DID (E.164 number) used for voice/SMS.
    Returns the channel row dict, or None.
    """
    if not external_id:
        return None
    client = get_client()
    res = await async_execute(
        client.table("channels")
        .select("id, business_id, channel_type, provider, external_id, display_name, status, config")
        .eq("provider", provider)
        .eq("external_id", external_id)
        .limit(1)
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


async def resolve_voice_or_sms_channel(
    *,
    provider: str,
    external_id: Optional[str],
    channel_type_hint: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """Return (channel_row, channel_type) best matching the inbound event.

    If the provider returned the same DID for voice + sms, prefer the row
    whose `channel_type` matches the hint (e.g. `phone` for call events,
    `sms` for message events). Falls back to any active row.
    """
    if not external_id:
        return None, channel_type_hint
    client = get_client()
    q = (
        client.table("channels")
        .select("id, business_id, channel_type, provider, external_id, display_name, status, config")
        .eq("provider", provider)
        .eq("external_id", external_id)
        .limit(5)
    )
    res = await async_execute(q)
    rows = getattr(res, "data", None) or []
    if not rows:
        return None, channel_type_hint

    if channel_type_hint:
        for row in rows:
            if row.get("channel_type") == channel_type_hint:
                return row, channel_type_hint
    return rows[0], rows[0].get("channel_type") or channel_type_hint


async def fetch_business(business_id: str) -> Optional[dict]:
    if not business_id:
        return None
    client = get_client()
    res = await async_execute(
        client.table("businesses")
        .select("id, name, slug, vertical, timezone, status, hours, escalation, settings")
        .eq("id", business_id)
        .limit(1)
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None
