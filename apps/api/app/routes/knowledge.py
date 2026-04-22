"""/v1/knowledge — CRUD for knowledge_items (FAQ, objection handling, etc.).

Create/update triggers a `knowledge-ingestion` job for embedding.

Schema note: column is ``content`` (not ``body``), ``type`` is an enum
(``faq | objection | product | policy | other``), and items have
``approved`` / ``review_required`` / ``tags`` fields.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


class KnowledgeItemCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    type: str = Field(default="faq", pattern=r"^(faq|objection|product|policy|other)$")
    tags: List[str] = Field(default_factory=list)
    active: bool = True
    approved: bool = False
    metadata: Optional[dict] = None


class KnowledgeItemUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    content: Optional[str] = Field(None, min_length=1)
    type: Optional[str] = Field(None, pattern=r"^(faq|objection|product|policy|other)$")
    tags: Optional[List[str]] = None
    active: Optional[bool] = None
    approved: Optional[bool] = None


@router.get("")
async def list_knowledge(
    type: Optional[str] = Query(None, alias="type"),
    active_only: bool = Query(True),
    approved_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    q = (
        client.table("knowledge_items")
        .select("id, title, content, type, tags, active, approved, review_required, created_at, updated_at", count="exact")
        .eq("business_id", user["business_id"])
    )
    if type:
        q = q.eq("type", type)
    if active_only:
        q = q.eq("active", True)
    if approved_only:
        q = q.eq("approved", True)
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
        .select("id, title, content, type, tags, active, approved, review_required, metadata, created_at, updated_at")
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
        "content": body.content,
        "type": body.type,
        "tags": body.tags,
        "active": body.active,
        "approved": body.approved,
        "review_required": not body.approved,
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

    if "title" in patch or "content" in patch:
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
