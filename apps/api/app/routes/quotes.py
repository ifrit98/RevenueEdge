"""/v1/quotes — list, detail, edit, approve, decline.

Approve triggers an outbound-actions job to send the quote to the customer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/quotes", tags=["quotes"])


class QuoteUpdate(BaseModel):
    draft_text: Optional[str] = None
    amount_low: Optional[float] = None
    amount_high: Optional[float] = None
    terms: Optional[str] = None
    notes: Optional[str] = None


class QuoteApprove(BaseModel):
    send_via: str = Field(default="sms", pattern=r"^(sms|email)$")


class QuoteDecline(BaseModel):
    decline_reason: Optional[str] = None


@router.get("")
async def list_quotes(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    q = (
        client.table("quotes")
        .select(
            "id, lead_id, contact_id, service_id, status, quote_type, "
            "amount_low, amount_high, draft_text, approved_by, sent_at, "
            "created_at, updated_at",
            count="exact",
        )
        .eq("business_id", user["business_id"])
    )
    if status:
        q = q.eq("status", status)
    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    res = await async_execute(q)
    return {
        "quotes": res.data or [],
        "total": getattr(res, "count", None) or len(res.data or []),
    }


@router.get("/{quote_id}")
async def get_quote(quote_id: str, user: dict = Depends(get_business_user)) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    res = await async_execute(
        client.table("quotes")
        .select("*")
        .eq("id", quote_id)
        .eq("business_id", user["business_id"])
        .limit(1)
    )
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Quote not found")
    quote = res.data[0]

    if quote.get("lead_id"):
        lead_res = await async_execute(
            client.table("leads")
            .select("id, stage, intake_fields, contact_id, conversation_id")
            .eq("id", quote["lead_id"])
            .limit(1)
        )
        quote["lead"] = (lead_res.data or [None])[0]
    if quote.get("contact_id"):
        contact_res = await async_execute(
            client.table("contacts")
            .select("id, name, phone_e164, email")
            .eq("id", quote["contact_id"])
            .limit(1)
        )
        quote["contact"] = (contact_res.data or [None])[0]
    return quote


@router.patch("/{quote_id}")
async def update_quote(
    quote_id: str,
    body: QuoteUpdate,
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = await async_execute(
        client.table("quotes")
        .update(patch)
        .eq("id", quote_id)
        .eq("business_id", user["business_id"])
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Quote not found")
    return rows[0]


@router.post("/{quote_id}/approve")
async def approve_quote(
    quote_id: str,
    body: QuoteApprove = QuoteApprove(),
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    res = await async_execute(
        client.table("quotes")
        .select("id, business_id, lead_id, contact_id, status, draft_text")
        .eq("id", quote_id)
        .eq("business_id", user["business_id"])
        .limit(1)
    )
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Quote not found")
    quote = res.data[0]
    if quote["status"] not in {"awaiting_review", "draft"}:
        raise HTTPException(status_code=409, detail=f"Quote is already {quote['status']}")

    now = datetime.now(timezone.utc).isoformat()
    await async_execute(
        client.table("quotes")
        .update({
            "status": "approved",
            "approved_by": user.get("user_id"),
            "approved_at": now,
            "updated_at": now,
        })
        .eq("id", quote_id)
    )

    conversation_id = None
    if quote.get("lead_id"):
        lead_res = await async_execute(
            client.table("leads").select("conversation_id").eq("id", quote["lead_id"]).limit(1)
        )
        if lead_res.data:
            conversation_id = lead_res.data[0].get("conversation_id")

    await async_execute(
        client.rpc(
            "enqueue_job",
            {
                "p_queue_name": "outbound-actions",
                "p_payload": {
                    "action": "send_quote",
                    "quote_id": quote_id,
                    "business_id": user["business_id"],
                    "contact_id": quote.get("contact_id"),
                    "conversation_id": conversation_id,
                    "lead_id": quote.get("lead_id"),
                    "send_via": body.send_via,
                    "reason": "quote_approved",
                },
                "p_business_id": user["business_id"],
                "p_idempotency_key": f"ob:quote:{quote_id}",
                "p_priority": 10,
            },
        )
    )

    return {"approved": True, "quote_id": quote_id}


@router.post("/{quote_id}/decline")
async def decline_quote(
    quote_id: str,
    body: QuoteDecline = QuoteDecline(),
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    now = datetime.now(timezone.utc).isoformat()
    res = await async_execute(
        client.table("quotes")
        .update({
            "status": "void",
            "metadata": {"decline_reason": body.decline_reason} if body.decline_reason else {},
            "updated_at": now,
        })
        .eq("id", quote_id)
        .eq("business_id", user["business_id"])
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Quote not found")
    return {"declined": True, "quote_id": quote_id}
