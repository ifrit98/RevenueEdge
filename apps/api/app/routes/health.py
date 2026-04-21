"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from ..db import get_supabase_client

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness probe: returns 200 if the process is alive."""
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.service_name,
        "environment": settings.environment,
        "release": settings.release,
    }


@router.get("/ready")
async def ready() -> dict:
    """Readiness probe: checks Supabase connectivity."""
    settings = get_settings()
    client = get_supabase_client()
    ready = client is not None and settings.supabase_configured
    return {
        "status": "ready" if ready else "not_ready",
        "supabase": bool(client),
    }
