"""Thin Supabase client wrapper for workers.

Shares a single service-role client across the process. All RPC/query calls
are dispatched via `asyncio.to_thread` since supabase-py is sync.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from supabase import Client, create_client

from .settings import get_worker_settings

logger = logging.getLogger(__name__)

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        s = get_worker_settings()
        if not s.supabase_url or not s.supabase_service_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for workers")
        _client = create_client(s.supabase_url, s.supabase_service_key)
        logger.info("Supabase service client initialized for workers")
    return _client


_DEFAULT_TIMEOUT = 30.0


async def async_execute(query: Any, *, timeout: float = _DEFAULT_TIMEOUT) -> Any:
    return await asyncio.wait_for(asyncio.to_thread(query.execute), timeout=timeout)


async def rpc(name: str, params: Optional[dict] = None, *, timeout: float = _DEFAULT_TIMEOUT) -> Any:
    """Call a Postgres RPC via Supabase."""
    client = get_client()
    return await asyncio.wait_for(
        asyncio.to_thread(client.rpc(name, params or {}).execute),
        timeout=timeout,
    )
