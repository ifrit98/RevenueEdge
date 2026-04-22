"""Tiny OpenAI JSON-mode client for the MVP.

We deliberately avoid the `openai` SDK here and hit the REST API directly
via httpx. This keeps workers portable and drops a dependency that would
otherwise collide with the deferred model-router work.

When `OPENAI_API_KEY` is empty, `classify_conversation` falls back to a
deterministic heuristic so dev/smoke tests still exercise the full queue
path without real LLM spend.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from ..settings import get_worker_settings

logger = logging.getLogger(__name__)

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

INTENT_VALUES = [
    "missed_call",
    "after_hours",
    "quote_request",
    "booking_request",
    "urgent_service",
    "support",
    "complaint",
    "reactivation",
    "reject",
    "handoff",
    "unknown",
]

URGENCY_VALUES = ["emergency", "same_day", "soon", "routine", "unknown"]

NEXT_ACTIONS = [
    "send_sms_reply",
    "ask_followup",
    "ask_question",
    "collect_quote_details",
    "draft_quote",
    "schedule_callback",
    "handoff",
    "mark_resolved",
    "noop",
]

DEFAULT_SYSTEM_PROMPT = """You are the Revenue Edge triage agent for a small business. Classify the customer's
most recent message and decide the safest next action.

Return STRICT JSON with this shape (no prose):
{
  "intent": one of INTENT_VALUES,
  "urgency": one of URGENCY_VALUES,
  "confidence": number in [0,1],
  "recommended_next_action": one of NEXT_ACTIONS,
  "reply_text": string (<=320 chars) OR empty,
  "fields_collected": { "field_name": "value" },
  "service_id": "uuid or null (matched service from ## Available Services)",
  "knowledge_missing": boolean (true only if knowledge section provided but no article covers the question),
  "handoff_reason": string (only if next_action is "handoff"),
  "summary": short recap <=200 chars
}

Policies:
- Never invent prices, arrival times, or appointment commitments.
- Emergencies, complaints, and sensitive topics → handoff.
- If confidence < 0.72 → recommend "handoff".
- Keep replies warm, brief, and oriented toward collecting the next useful detail.
- If a "## Business Knowledge" section is provided below, answer ONLY from those articles.
  If no article covers the customer's question, set "knowledge_missing": true in the response
  and reply with: "I want to make sure I give you the right answer. Let me have the team confirm and get back to you."

## Field Collection (quote_request or booking_request)
When the intent is quote_request or booking_request:
1. Match the customer's request to a service from the "## Available Services" section below.
2. Check which required_intake_fields for that service are still missing (compare against fields_collected).
3. Set recommended_next_action = "ask_question" and write reply_text asking for exactly ONE missing field at a time.
4. When all required fields are collected, set recommended_next_action = "draft_quote".
5. If the customer provides unsolicited fields, capture them in fields_collected anyway.
6. Include each extracted field in "fields_collected" keyed by the field name (e.g. "name", "address", "scope").
""".replace(
    "INTENT_VALUES", ", ".join(f'"{v}"' for v in INTENT_VALUES)
).replace("URGENCY_VALUES", ", ".join(f'"{v}"' for v in URGENCY_VALUES)).replace(
    "NEXT_ACTIONS", ", ".join(f'"{v}"' for v in NEXT_ACTIONS)
)


def _format_messages_for_llm(context: dict[str, Any]) -> list[dict[str, str]]:
    business = context.get("business") or {}
    contact = context.get("contact") or {}
    conversation = context.get("conversation") or {}
    history = context.get("messages") or []

    kb_articles = context.get("knowledge_articles") or []

    header_lines = [
        f"Business: {business.get('name') or '(unknown)'}",
        f"Vertical: {business.get('vertical') or 'other'}",
        f"Timezone: {business.get('timezone') or 'America/New_York'}",
        f"Channel: {conversation.get('channel_type') or 'unknown'}",
        f"Contact: name={contact.get('name') or '?'} phone={contact.get('phone_e164') or '?'}",
        f"Current intent: {conversation.get('current_intent') or '(none)'}",
    ]
    transcript_lines = []
    for m in history[-20:]:
        role = m.get("direction") or "?"
        who = m.get("sender_type") or "?"
        body = (m.get("body") or m.get("normalized_body") or "").strip()
        if not body:
            continue
        transcript_lines.append(f"- [{role}/{who}] {body}")

    sections = [
        "Context:\n" + "\n".join(header_lines),
        "\nRecent messages (oldest → newest):\n"
        + ("\n".join(transcript_lines) if transcript_lines else "(no prior messages)"),
    ]

    if kb_articles:
        kb_lines = ["## Business Knowledge"]
        for i, art in enumerate(kb_articles[:5], 1):
            title = art.get("title") or "(untitled)"
            body_text = (art.get("content") or art.get("body") or "")[:600]
            kb_lines.append(f"\n### Article {i}: {title}\n{body_text}")
        sections.append("\n".join(kb_lines))

    services = context.get("services") or []
    if services:
        svc_lines = ["## Available Services"]
        for svc in services[:10]:
            req = ", ".join(svc.get("required_intake_fields") or []) or "(none)"
            low = svc.get("base_price_low")
            high = svc.get("base_price_high")
            price = f"${low}–${high}" if low and high else f"from ${low}" if low else "TBD"
            svc_lines.append(
                f"- {svc['name']} (id={svc['id']}): "
                f"price={price}, required_fields=[{req}]"
            )
        sections.append("\n".join(svc_lines))

    sections.append("\nRespond ONLY with the JSON object described in the system prompt.")
    user_prompt = "\n".join(sections)

    return [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _heuristic_fallback(context: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fallback when we have no API key. Keeps the smoke test honest."""
    conv = context.get("conversation") or {}
    intent = conv.get("current_intent") or "unknown"
    source_event = (context.get("source_event_type") or "").lower()
    if source_event == "call.missed":
        intent = "missed_call"
    elif source_event == "message.received":
        intent = intent if intent != "unknown" else "support"
    return {
        "intent": intent,
        "urgency": "routine",
        "confidence": 0.55,
        "recommended_next_action": "send_sms_reply" if intent == "missed_call" else "handoff",
        "reply_text": "" if intent != "missed_call" else "Thanks for reaching out — what can we help with today?",
        "fields_collected": {},
        "handoff_reason": "LLM disabled — conservative default",
        "summary": f"heuristic classification for event={source_event or 'unknown'}",
    }


async def classify_conversation(context: dict[str, Any]) -> dict[str, Any]:
    """Ask the LLM to classify the active conversation.

    `context` is expected to include `business`, `contact`, `conversation`,
    `messages`, and optionally `source_event_type` (canonical queue event).
    """
    settings = get_worker_settings()
    if not settings.openai_api_key:
        logger.info("OPENAI_API_KEY missing — using heuristic classifier")
        return _heuristic_fallback(context)

    body = {
        "model": settings.llm_chat_model,
        "messages": _format_messages_for_llm(context),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(_OPENAI_CHAT_URL, headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        logger.warning("LLM call failed (%s) — falling back to heuristic", exc)
        return _heuristic_fallback(context)

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM returned unparseable JSON (%s) — falling back", exc)
        return _heuristic_fallback(context)

    parsed.setdefault("intent", "unknown")
    parsed.setdefault("urgency", "unknown")
    parsed.setdefault("confidence", 0.0)
    parsed.setdefault("recommended_next_action", "handoff")
    parsed.setdefault("fields_collected", {})
    parsed.setdefault("reply_text", "")
    parsed.setdefault("summary", "")
    parsed["_model"] = settings.llm_chat_model
    parsed["_usage"] = data.get("usage") or {}

    if parsed["intent"] not in INTENT_VALUES:
        parsed["intent"] = "unknown"
    if parsed["urgency"] not in URGENCY_VALUES:
        parsed["urgency"] = "unknown"
    if parsed["recommended_next_action"] not in NEXT_ACTIONS:
        parsed["recommended_next_action"] = "handoff"

    return parsed


def coerce_confidence(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f < 0:
        return 0.0
    if f > 1:
        return 1.0
    return round(f, 3)
