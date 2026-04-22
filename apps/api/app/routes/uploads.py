"""/v1/uploads — Signed upload URLs for customer photo/document submissions.

The fallback path when Retell MMS isn't available: send the customer an SMS
with a link to ``/upload/<token>``, which redirects to a signed Supabase
Storage upload URL.  The upload lands in ``photos/<business_id>/<token>/``
and we attach the resulting URL to the conversation.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
STORAGE_BUCKET = "photos"
UPLOAD_LINK_TTL_HOURS = 48


class UploadLinkRequest(BaseModel):
    conversation_id: str
    contact_id: Optional[str] = None
    purpose: str = Field(default="photo_request", max_length=100)


class UploadLinkResponse(BaseModel):
    upload_url: str
    token: str
    expires_at: str


@router.post("/request-link", response_model=UploadLinkResponse)
async def create_upload_link(
    body: UploadLinkRequest,
    user: dict = Depends(get_business_user),
) -> UploadLinkResponse:
    """Generate a one-time upload token and return a URL the customer can use."""
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    token = uuid.uuid4().hex
    business_id = user["business_id"]
    expires = datetime.now(timezone.utc) + timedelta(hours=UPLOAD_LINK_TTL_HOURS)

    row = {
        "id": token,
        "business_id": business_id,
        "conversation_id": body.conversation_id,
        "contact_id": body.contact_id,
        "purpose": body.purpose,
        "expires_at": expires.isoformat(),
        "used": False,
        "metadata": {},
    }
    await async_execute(client.table("upload_tokens").insert(row))

    api_base = os.getenv("PUBLIC_API_URL", SUPABASE_URL.replace(".supabase.co", "")).rstrip("/")
    upload_url = f"{api_base}/v1/uploads/{token}"

    return UploadLinkResponse(
        upload_url=upload_url,
        token=token,
        expires_at=expires.isoformat(),
    )


@router.get("/{token}")
async def get_upload_info(token: str) -> dict:
    """Public endpoint: validate an upload token and return storage info.

    The frontend upload page calls this to get the signed URL for direct
    upload to Supabase Storage.
    """
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    res = await async_execute(
        client.table("upload_tokens")
        .select("*")
        .eq("id", token)
        .limit(1)
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Upload link not found or expired")

    row = rows[0]
    if row.get("used"):
        raise HTTPException(status_code=410, detail="This upload link has already been used")

    expires_at = row.get("expires_at", "")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Upload link has expired")
        except (ValueError, TypeError):
            pass

    storage_path = f"{row['business_id']}/{token}"

    return {
        "token": token,
        "bucket": STORAGE_BUCKET,
        "storage_path": storage_path,
        "supabase_url": SUPABASE_URL,
        "purpose": row.get("purpose"),
        "expires_at": expires_at,
    }


@router.post("/{token}/complete")
async def mark_upload_complete(
    token: str,
    file_url: str = Query(..., description="Public URL of uploaded file"),
) -> dict:
    """Called after the customer finishes uploading to mark the token used
    and attach the file to the conversation."""
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    res = await async_execute(
        client.table("upload_tokens")
        .select("*")
        .eq("id", token)
        .eq("used", False)
        .limit(1)
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Token not found or already used")

    row = rows[0]
    now = datetime.now(timezone.utc).isoformat()

    await async_execute(
        client.table("upload_tokens")
        .update({"used": True, "metadata": {**(row.get("metadata") or {}), "file_url": file_url, "completed_at": now}})
        .eq("id", token)
    )

    conversation_id = row.get("conversation_id")
    business_id = row.get("business_id")
    if conversation_id and business_id:
        from .conversations import _insert_message_from_api
        try:
            await _insert_message_from_api(
                business_id=business_id,
                conversation_id=conversation_id,
                contact_id=row.get("contact_id"),
                body=f"[Customer uploaded photo: {file_url}]",
                direction="inbound",
                sender_type="customer",
                raw_payload={"upload_token": token, "file_url": file_url, "purpose": row.get("purpose")},
            )
        except Exception:
            logger.warning("Failed to insert upload message into conversation", exc_info=True)

    return {"ok": True, "token": token, "file_url": file_url}
