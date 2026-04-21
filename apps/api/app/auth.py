"""Authentication dependencies.

JWT-first authentication with internal-service-key fallback for worker-to-api
and webhook-to-api calls.

Ported from SMB-MetaPattern/apps/api-gateway/app/auth.py with:
  - `tenants` / `tenant_users` renamed to `businesses` / `business_members`
  - `tenant_id` renamed to `business_id`
  - Dashboard header hint `x-tenant-id` → `x-business-id`
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional, Tuple

import jwt  # PyJWT
from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
INTERNAL_SERVICE_KEY = os.getenv("INTERNAL_SERVICE_KEY", "")
INTERNAL_TOOL_USER_ID = os.getenv("INTERNAL_TOOL_USER_ID", "").strip()
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

_BUSINESS_CACHE: Dict[str, Tuple[str, float]] = {}
_CACHE_TTL_SECONDS = 300


def _clear_business_cache() -> None:
    _BUSINESS_CACHE.clear()


async def verify_supabase_jwt(token: str) -> dict:
    if not SUPABASE_JWT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Server misconfiguration: JWT secret not set",
        )
    try:
        return jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


async def _resolve_business_for_user(user_id: str) -> str:
    """Return the user's oldest `business_members.business_id`.

    Results cached with a 5-minute TTL.
    """
    now = time.time()
    cached = _BUSINESS_CACHE.get(user_id)
    if cached and cached[1] > now:
        return cached[0]

    from .db import async_execute, get_supabase_client

    supabase = get_supabase_client()
    if supabase is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    result = await async_execute(
        supabase.table("business_members")
        .select("business_id, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .limit(2)
    )

    if not result.data:
        raise HTTPException(
            status_code=403,
            detail="User is not associated with any business",
        )

    if len(result.data) > 1:
        logger.warning(
            "Ambiguous business resolution for user_id=%s: %d memberships; "
            "defaulting to oldest (business_id=%s). Pass X-Business-ID to disambiguate.",
            user_id,
            len(result.data),
            result.data[0]["business_id"],
        )

    business_id: str = result.data[0]["business_id"]
    _BUSINESS_CACHE[user_id] = (business_id, now + _CACHE_TTL_SECONDS)
    return business_id


async def _user_has_business_access(user_id: str, business_id: str) -> bool:
    from .db import async_execute, get_supabase_client

    supabase = get_supabase_client()
    if supabase is None:
        return False
    result = await async_execute(
        supabase.table("business_members")
        .select("business_id")
        .eq("user_id", user_id)
        .eq("business_id", business_id)
        .limit(1)
    )
    return bool(result.data)


async def _resolve_business_for_jwt_user(
    user_id: str, x_business_id: Optional[str]
) -> str:
    if x_business_id and x_business_id.strip():
        if await _user_has_business_access(user_id, x_business_id.strip()):
            return x_business_id.strip()
        raise HTTPException(
            status_code=403,
            detail="User is not a member of the requested business",
        )
    return await _resolve_business_for_user(user_id)


def _is_header_fallback_allowed(request: Request) -> bool:
    if ENVIRONMENT == "test":
        return True
    internal_key = request.headers.get("x-internal-key", "")
    if INTERNAL_SERVICE_KEY and internal_key == INTERNAL_SERVICE_KEY:
        return True
    return False


def _internal_key_matches(request: Request) -> bool:
    internal_key = request.headers.get("x-internal-key", "")
    return bool(INTERNAL_SERVICE_KEY and internal_key == INTERNAL_SERVICE_KEY)


async def get_business_user(
    request: Request,
    x_business_id: Optional[str] = Header(None, alias="x-business-id"),
    x_user_id: Optional[str] = Header(None, alias="x-user-id"),
) -> dict:
    """Return `{"business_id": str, "user_id": str}`.

    Priority:
      1. `Authorization: Bearer <jwt>` → verify and resolve membership.
      2. `X-Business-ID` + `X-User-ID` headers, gated on test env or valid
         `X-Internal-Key`.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        claims = await verify_supabase_jwt(token)
        user_id = claims.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="JWT missing sub claim")
        business_id = await _resolve_business_for_jwt_user(user_id, x_business_id)
        return {"business_id": business_id, "user_id": user_id}

    if _is_header_fallback_allowed(request):
        if not x_business_id:
            raise HTTPException(status_code=400, detail="Missing business header")
        uid = x_user_id
        if not uid and _internal_key_matches(request) and INTERNAL_TOOL_USER_ID:
            uid = INTERNAL_TOOL_USER_ID
        if not uid:
            raise HTTPException(
                status_code=400,
                detail="Missing business or user headers",
            )
        return {"business_id": x_business_id, "user_id": uid}

    raise HTTPException(status_code=401, detail="Authentication required")


async def get_business_id(
    request: Request,
    x_business_id: Optional[str] = Header(None, alias="x-business-id"),
) -> str:
    """Return just the business_id.

    Same priority order as `get_business_user`.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        claims = await verify_supabase_jwt(token)
        user_id = claims.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="JWT missing sub claim")
        return await _resolve_business_for_jwt_user(user_id, x_business_id)

    if _is_header_fallback_allowed(request):
        if x_business_id:
            return x_business_id
        raise HTTPException(status_code=400, detail="Missing business header")

    raise HTTPException(status_code=401, detail="Authentication required")


def require_internal_key(request: Request) -> None:
    """FastAPI dependency: allow only callers presenting a valid internal key.

    Used by internal endpoints that workers call.
    """
    if not _internal_key_matches(request):
        raise HTTPException(status_code=401, detail="Internal key required")
