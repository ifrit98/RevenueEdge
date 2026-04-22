"""/v1/services — CRUD for business services (used by quote intake flow).

Each service defines required_intake_fields and optional pricing ranges.
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

router = APIRouter(prefix="/v1/services", tags=["services"])


class ServiceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    base_price_low: Optional[float] = None
    base_price_high: Optional[float] = None
    required_intake_fields: List[str] = Field(default_factory=lambda: ["name", "phone", "address", "scope"])
    tags: List[str] = Field(default_factory=list)
    active: bool = True
    metadata: Optional[dict] = None


class ServiceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    base_price_low: Optional[float] = None
    base_price_high: Optional[float] = None
    required_intake_fields: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    active: Optional[bool] = None


@router.get("")
async def list_services(
    active_only: bool = Query(True),
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    q = (
        client.table("services")
        .select("id, name, description, base_price_low, base_price_high, required_intake_fields, tags, active, metadata, created_at")
        .eq("business_id", user["business_id"])
    )
    if active_only:
        q = q.eq("active", True)
    q = q.order("name", desc=False)
    res = await async_execute(q)
    return {"services": res.data or []}


@router.get("/{service_id}")
async def get_service(service_id: str, user: dict = Depends(get_business_user)) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    res = await async_execute(
        client.table("services")
        .select("*")
        .eq("id", service_id)
        .eq("business_id", user["business_id"])
        .limit(1)
    )
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Service not found")
    return res.data[0]


@router.post("", status_code=201)
async def create_service(
    body: ServiceCreate,
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    row = {
        "business_id": user["business_id"],
        "name": body.name,
        "description": body.description or "",
        "base_price_low": body.base_price_low,
        "base_price_high": body.base_price_high,
        "required_intake_fields": body.required_intake_fields,
        "tags": body.tags,
        "active": body.active,
        "metadata": body.metadata or {},
    }
    res = await async_execute(client.table("services").insert(row))
    created = (res.data or [None])[0]
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create service")
    return created


@router.patch("/{service_id}")
async def update_service(
    service_id: str,
    body: ServiceUpdate,
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
        client.table("services")
        .update(patch)
        .eq("id", service_id)
        .eq("business_id", user["business_id"])
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Service not found")
    return rows[0]
