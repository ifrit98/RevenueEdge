"""Contact upsert helpers.

We insert-first-then-select via the unique partial indexes on
`contacts(business_id, phone_e164)` / `contacts(business_id, email)`.
supabase-py doesn't expose native `on conflict` cleanly, so we do a
lookup → insert → re-lookup pattern.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..supabase_client import async_execute, get_client

logger = logging.getLogger(__name__)


async def upsert_contact(
    *,
    business_id: str,
    phone_e164: Optional[str] = None,
    email: Optional[str] = None,
    name: Optional[str] = None,
    source_channel: Optional[str] = None,
) -> Optional[dict]:
    if not business_id:
        return None
    if not phone_e164 and not email:
        return None

    client = get_client()

    existing = None
    if phone_e164:
        res = await async_execute(
            client.table("contacts")
            .select("id, business_id, name, phone_e164, email, tags, metadata")
            .eq("business_id", business_id)
            .eq("phone_e164", phone_e164)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        existing = rows[0] if rows else None

    if existing is None and email:
        res = await async_execute(
            client.table("contacts")
            .select("id, business_id, name, phone_e164, email, tags, metadata")
            .eq("business_id", business_id)
            .eq("email", email)
            .limit(1)
        )
        rows = getattr(res, "data", None) or []
        existing = rows[0] if rows else None

    if existing:
        # Opportunistically fill in a missing name/phone/email.
        patch: dict = {}
        if name and not existing.get("name"):
            patch["name"] = name
        if phone_e164 and not existing.get("phone_e164"):
            patch["phone_e164"] = phone_e164
        if email and not existing.get("email"):
            patch["email"] = email
        if patch:
            await async_execute(
                client.table("contacts").update(patch).eq("id", existing["id"])
            )
            existing.update(patch)
        return existing

    insert_row: dict = {
        "business_id": business_id,
        "name": name,
        "phone_e164": phone_e164,
        "email": email,
        "source_channel": source_channel,
    }
    try:
        res = await async_execute(
            client.table("contacts").insert({k: v for k, v in insert_row.items() if v is not None})
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return rows[0]
    except Exception as exc:
        err_str = str(exc).lower()
        if "unique" in err_str or "duplicate" in err_str or "23505" in err_str:
            logger.info("Contact insert hit unique constraint — re-querying")
            if phone_e164:
                res = await async_execute(
                    client.table("contacts")
                    .select("id, business_id, name, phone_e164, email, tags, metadata")
                    .eq("business_id", business_id)
                    .eq("phone_e164", phone_e164)
                    .limit(1)
                )
                rows = getattr(res, "data", None) or []
                if rows:
                    return rows[0]
            if email:
                res = await async_execute(
                    client.table("contacts")
                    .select("id, business_id, name, phone_e164, email, tags, metadata")
                    .eq("business_id", business_id)
                    .eq("email", email)
                    .limit(1)
                )
                rows = getattr(res, "data", None) or []
                if rows:
                    return rows[0]
        else:
            raise

    logger.warning(
        "Contact insert returned no rows", extra={"business_id": business_id, "phone": phone_e164}
    )
    return None
