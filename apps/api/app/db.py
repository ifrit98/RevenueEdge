"""Supabase client singleton and async execution helpers.

Ported verbatim from SMB-MetaPattern/apps/api-gateway/app/db.py with minor
cleanup (no prometheus histogram dependency by default — re-add in Phase 5
when observability is wired up).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from supabase import Client, create_client

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv(
    "SUPABASE_SERVICE_KEY", os.getenv("SUPABASE_SERVICE_ROLE", "")
)
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
ENVIRONMENT = os.getenv("ENVIRONMENT", "")


class SupabaseClientManager:
    """Lazy singleton for service-role and anon Supabase clients."""

    _service_client: Optional[Client] = None
    _anon_client: Optional[Client] = None

    @classmethod
    def get_service_client(cls) -> Optional[Client]:
        if cls._service_client is None:
            if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
                if ENVIRONMENT == "test":
                    return None
                logger.warning("Supabase service client not configured")
                return None
            try:
                cls._service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
                logger.info("Supabase service client initialized")
            except Exception as exc:
                logger.error("Failed to create Supabase service client: %s", exc)
                return None
        return cls._service_client

    @classmethod
    def get_anon_client(cls) -> Optional[Client]:
        if cls._anon_client is None:
            if not SUPABASE_URL or not SUPABASE_ANON_KEY:
                if ENVIRONMENT == "test":
                    return None
                logger.warning("Supabase anon client not configured")
                return None
            try:
                cls._anon_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
                logger.info("Supabase anon client initialized")
            except Exception as exc:
                logger.error("Failed to create Supabase anon client: %s", exc)
                return None
        return cls._anon_client

    @classmethod
    def reset(cls) -> None:
        cls._service_client = None
        cls._anon_client = None


def get_supabase_client() -> Optional[Client]:
    return SupabaseClientManager.get_service_client()


def get_supabase_service_client() -> Optional[Client]:
    return SupabaseClientManager.get_service_client()


def get_supabase_anon_client() -> Optional[Client]:
    return SupabaseClientManager.get_anon_client()


def get_db() -> Client:
    """FastAPI dependency. Raises 503 if Supabase is unconfigured."""
    from fastapi import HTTPException

    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database service unavailable")
    return client


async def async_execute(query: Any) -> Any:
    """Run a Supabase query builder's .execute() in a worker thread.

    Supabase's Python client is sync; direct calls would block the event loop.
    """
    return await asyncio.to_thread(query.execute)


async def async_execute_with_timeout(query: Any, timeout_seconds: float = 30.0) -> Any:
    return await asyncio.wait_for(
        asyncio.to_thread(query.execute),
        timeout=timeout_seconds,
    )


async def rpc(name: str, params: Optional[dict] = None) -> Any:
    """Call a Postgres RPC via Supabase. Returns the raw response object.

    Example:
        await rpc("claim_queue_jobs", {
            "p_queue_name": "inbound-events",
            "p_worker_id": "worker-1",
            "p_limit": 5,
        })
    """
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase client not configured")
    return await asyncio.to_thread(client.rpc(name, params or {}).execute)


__all__ = [
    "SupabaseClientManager",
    "get_supabase_client",
    "get_supabase_service_client",
    "get_supabase_anon_client",
    "get_db",
    "async_execute",
    "async_execute_with_timeout",
    "rpc",
]
