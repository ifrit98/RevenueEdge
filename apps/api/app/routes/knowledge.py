"""/v1/knowledge — CRUD for knowledge_items (FAQ, objection handling, etc.).

Create/update triggers a `knowledge-ingestion` job for embedding.
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

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


class KnowledgeItemCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    body: str = Field(..., min_length=1)
    category: str = Field(default="faq", max_length=100)
    active: bool = True
    metadata: Optional[dict] = None


class KnowledgeItemUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    body: Optional[str] = Field(None, min_length=1)
    category: Optional[str] = Field(None, max_length=100)
    active: Optional[bool] = None


@router.get("")
async def list_knowledge(
    category: Optional[str] = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    q = (
        client.table("knowledge_items")
        .select("id, title, body, category, active, created_at, updated_at", count="exact")
        .eq("business_id", user["business_id"])
    )
    if category:
        q = q.eq("category", category)
    if active_only:
        q = q.eq("active", True)
    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    res = await async_execute(q)
    return {
        "items": res.data or [],
        "total": getattr(res, "count", None) or len(res.data or []),
    }


@router.get("/{item_id}")
async def get_knowledge_item(item_id: str, user: dict = Depends(get_business_user)) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    res = await async_execute(
        client.table("knowledge_items")
        .select("id, title, body, category, active, metadata, created_at, updated_at")
        .eq("id", item_id)
        .eq("business_id", user["business_id"])
        .limit(1)
    )
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return res.data[0]


@router.post("", status_code=201)
async def create_knowledge_item(
    body: KnowledgeItemCreate,
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    row = {
        "business_id": user["business_id"],
        "title": body.title,
        "body": body.body,
        "category": body.category,
        "active": body.active,
        "metadata": body.metadata or {},
    }
    res = await async_execute(client.table("knowledge_items").insert(row))
    created = (res.data or [None])[0]
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create knowledge item")

    await _enqueue_ingestion(client, created["id"], user["business_id"], action="embed")
    return created


@router.patch("/{item_id}")
async def update_knowledge_item(
    item_id: str,
    body: KnowledgeItemUpdate,
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
        client.table("knowledge_items")
        .update(patch)
        .eq("id", item_id)
        .eq("business_id", user["business_id"])
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Knowledge item not found")

    if "title" in patch or "body" in patch:
        await _enqueue_ingestion(client, item_id, user["business_id"], action="re_embed")
    return rows[0]


@router.delete("/{item_id}", status_code=204)
async def delete_knowledge_item(
    item_id: str,
    user: dict = Depends(get_business_user),
) -> None:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    await async_execute(
        client.table("knowledge_items")
        .update({"active": False, "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", item_id)
        .eq("business_id", user["business_id"])
    )


async def _enqueue_ingestion(client, item_id: str, business_id: str, action: str = "embed") -> None:
    """Fire a knowledge-ingestion job via the queue RPC."""
    try:
        await async_execute(
            client.rpc(
                "enqueue_job",
                {
                    "p_queue_name": "knowledge-ingestion",
                    "p_payload": {
                        "knowledge_item_id": item_id,
                        "business_id": business_id,
                        "action": action,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ki:{action}:{item_id}",
                    "p_priority": 50,
                },
            )
        )
    except Exception:
        logger.warning("Failed to enqueue knowledge ingestion job", exc_info=True)
