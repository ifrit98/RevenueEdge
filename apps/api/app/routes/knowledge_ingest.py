"""/v1/knowledge/ingest — Bulk knowledge ingestion from external sources.

Three ingestion modes:
  1. ``POST /v1/knowledge/ingest/website``  — crawl a URL, extract text, chunk, create items
  2. ``POST /v1/knowledge/ingest/document`` — upload PDF/DOCX/TXT, parse, chunk, create items
  3. ``POST /v1/knowledge/ingest/google-docs`` — fetch a Google Doc by ID, parse, chunk, create items

All modes create ``knowledge_items`` rows in ``review_required=true`` state and
enqueue embedding jobs for each chunk.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/knowledge/ingest", tags=["knowledge-ingestion"])

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


class WebsiteIngestRequest(BaseModel):
    url: str = Field(..., min_length=8, description="Root URL to crawl")
    max_pages: int = Field(default=20, ge=1, le=100)
    type: str = Field(default="product", pattern=r"^(faq|objection|product|policy|other)$")


class GoogleDocIngestRequest(BaseModel):
    doc_id: str = Field(..., min_length=10, pattern=r"^[a-zA-Z0-9_-]+$", description="Google Docs document ID")
    type: str = Field(default="product", pattern=r"^(faq|objection|product|policy|other)$")


@router.post("/website")
async def ingest_website(
    body: WebsiteIngestRequest,
    user: dict = Depends(get_business_user),
) -> dict:
    """Enqueue a website scrape job. Processing happens in the worker."""
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    business_id = user["business_id"]
    await _enqueue_ingestion_job(
        client,
        business_id=business_id,
        action="scrape_website",
        payload={
            "url": body.url,
            "max_pages": body.max_pages,
            "type": body.type,
        },
    )
    return {"queued": True, "action": "scrape_website", "url": body.url, "max_pages": body.max_pages}


@router.post("/document")
async def ingest_document(
    file: UploadFile = File(...),
    type: str = Form(default="product"),
    user: dict = Depends(get_business_user),
) -> dict:
    """Upload a PDF/DOCX/TXT file. Parse, chunk, and create knowledge items inline."""
    _VALID_TYPES = {"faq", "objection", "product", "policy", "other"}
    if type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid type: must be one of {_VALID_TYPES}")

    if file.size and file.size > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large (10 MB max)")

    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large (10 MB max)")

    business_id = user["business_id"]
    filename = file.filename or "upload.bin"

    try:
        items = await _parse_and_store(
            data=data,
            filename=filename,
            content_type=file.content_type,
            business_id=business_id,
            item_type=type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Document parse failed for %s: %s", filename, exc, exc_info=True)
        raise HTTPException(status_code=422, detail=f"Failed to parse document: {exc.__class__.__name__}")

    return {"created": len(items), "items": [{"id": i["id"], "title": i["title"]} for i in items]}


@router.post("/google-docs")
async def ingest_google_doc(
    body: GoogleDocIngestRequest,
    user: dict = Depends(get_business_user),
) -> dict:
    """Enqueue a Google Docs fetch job. Processing happens in the worker."""
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    business_id = user["business_id"]
    await _enqueue_ingestion_job(
        client,
        business_id=business_id,
        action="fetch_google_doc",
        payload={
            "doc_id": body.doc_id,
            "type": body.type,
        },
    )
    return {"queued": True, "action": "fetch_google_doc", "doc_id": body.doc_id}


async def _parse_and_store(
    *,
    data: bytes,
    filename: str,
    content_type: Optional[str],
    business_id: str,
    item_type: str,
) -> list[dict]:
    """Parse document bytes, chunk, and insert knowledge items."""
    # Lazy imports so the API doesn't hard-depend on worker libs at module load
    import sys, os
    workers_src = os.path.join(os.path.dirname(__file__), "..", "..", "..", "workers", "src")
    if workers_src not in sys.path:
        sys.path.insert(0, os.path.abspath(workers_src))

    from lib.doc_parser import extract_text_from_bytes  # type: ignore
    from lib.text_chunker import chunk_text  # type: ignore

    text = await extract_text_from_bytes(data, filename, content_type)
    if not text or len(text.strip()) < 50:
        raise ValueError("Document contains too little extractable text")

    chunks = chunk_text(text, title_prefix=filename.rsplit(".", 1)[0])
    if not chunks:
        raise ValueError("No content chunks produced from document")

    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    created: list[dict] = []
    for chunk in chunks:
        row = {
            "business_id": business_id,
            "title": chunk["title"][:500],
            "content": chunk["content"],
            "type": item_type,
            "tags": ["auto_imported", f"source:{filename}"],
            "active": True,
            "approved": False,
            "review_required": True,
            "metadata": {"source": "document_upload", "filename": filename},
        }
        res = await async_execute(client.table("knowledge_items").insert(row))
        item = (res.data or [None])[0]
        if item:
            created.append(item)
            await _enqueue_embed(client, item["id"], business_id)

    return created


async def _enqueue_ingestion_job(client, *, business_id: str, action: str, payload: dict) -> None:
    try:
        await async_execute(
            client.rpc(
                "enqueue_job",
                {
                    "p_queue_name": "knowledge-ingestion",
                    "p_payload": {
                        "business_id": business_id,
                        "action": action,
                        **payload,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ki:{action}:{business_id}:{payload.get('url') or payload.get('doc_id') or 'unknown'}",
                    "p_priority": 50,
                },
            )
        )
    except Exception:
        logger.warning("Failed to enqueue knowledge ingestion job", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to enqueue ingestion job")


async def _enqueue_embed(client, item_id: str, business_id: str) -> None:
    try:
        await async_execute(
            client.rpc(
                "enqueue_job",
                {
                    "p_queue_name": "knowledge-ingestion",
                    "p_payload": {
                        "knowledge_item_id": item_id,
                        "business_id": business_id,
                        "action": "embed",
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ki:embed:{item_id}",
                    "p_priority": 50,
                },
            )
        )
    except Exception:
        logger.warning("Failed to enqueue embed job for %s", item_id, exc_info=True)
