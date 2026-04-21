"""Message template rendering.

The seed data uses Liquid-ish placeholders like `{{business.name}}` and
`{{contact.first_name | default: "there"}}`. For the MVP we support a
deliberately tiny subset:

  - `{{path.to.field}}` — dotted path lookup against the context dict
  - `{{path.to.field | default: "fallback"}}` — fallback string if missing/empty

This avoids adding a full Jinja/Liquid dep while matching the seed grammar.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..supabase_client import async_execute, get_client

logger = logging.getLogger(__name__)


_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_DEFAULT_LITERAL = re.compile(r'^default\s*:\s*"([^"]*)"$')


def _walk(data: Any, dotted_path: str) -> Any:
    cur: Any = data
    for part in dotted_path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return cur


def render_template(template: str, context: dict[str, Any]) -> str:
    def _replace(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        default_value = ""
        path = inner
        if "|" in inner:
            segments = [s.strip() for s in inner.split("|")]
            path = segments[0]
            for seg in segments[1:]:
                m = _DEFAULT_LITERAL.match(seg)
                if m:
                    default_value = m.group(1)
                    break
        value = _walk(context, path)
        if value is None or (isinstance(value, str) and not value.strip()):
            return default_value
        return str(value)

    return _PLACEHOLDER.sub(_replace, template)


async def load_template(
    *,
    business_id: str,
    name: Optional[str] = None,
    intent: Optional[str] = None,
    channel_type: Optional[str] = None,
) -> Optional[dict]:
    """Resolve a `message_templates` row.

    Priority: exact `name` match > (intent + channel_type) > intent-only.
    Returns None if nothing active matches.
    """
    client = get_client()

    async def _query(**filters: Any) -> Optional[dict]:
        q = (
            client.table("message_templates")
            .select("id, name, channel_type, intent, body_template, metadata")
            .eq("business_id", business_id)
            .eq("active", True)
        )
        for k, v in filters.items():
            q = q.eq(k, v)
        res = await async_execute(q.limit(1))
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None

    if name:
        row = await _query(name=name)
        if row:
            return row
    if intent and channel_type:
        row = await _query(intent=intent, channel_type=channel_type)
        if row:
            return row
    if intent:
        row = await _query(intent=intent)
        if row:
            return row
    return None


def build_render_context(
    *,
    business: Optional[dict],
    contact: Optional[dict],
    conversation: Optional[dict],
    lead: Optional[dict] = None,
) -> dict[str, Any]:
    first_name = None
    if contact and contact.get("name"):
        first_name = contact["name"].strip().split(" ")[0] if contact["name"] else None
    return {
        "business": {
            "name": (business or {}).get("name"),
            "slug": (business or {}).get("slug"),
            "timezone": (business or {}).get("timezone"),
        },
        "contact": {
            "name": (contact or {}).get("name"),
            "first_name": first_name,
            "phone_e164": (contact or {}).get("phone_e164"),
            "email": (contact or {}).get("email"),
        },
        "conversation": {
            "id": (conversation or {}).get("id"),
            "current_intent": (conversation or {}).get("current_intent"),
            "urgency": (conversation or {}).get("urgency"),
        },
        "lead": {
            "service_requested": (lead or {}).get("service_requested"),
            "stage": (lead or {}).get("stage"),
        },
    }
