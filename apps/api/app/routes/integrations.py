"""/v1/integrations — Google Calendar OAuth + future integrations.

OAuth 2.0 authorization code flow with PKCE:
  1. GET  /v1/integrations/google-calendar/auth-url  → redirect URL
  2. GET  /v1/integrations/google-calendar/callback   → stores tokens
  3. GET  /v1/integrations/google-calendar/status      → connected?
  4. DELETE /v1/integrations/google-calendar           → disconnect
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import get_business_user
from ..config import get_settings
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.readonly"


def _get_google_creds() -> tuple[str, str]:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")
    return client_id, client_secret


def _callback_url(request: Request) -> str:
    override = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    if override:
        return override
    return str(request.url_for("google_calendar_callback"))


@router.get("/google-calendar/auth-url")
async def google_calendar_auth_url(
    request: Request,
    user: dict = Depends(get_business_user),
) -> dict:
    client_id, _ = _get_google_creds()
    import base64

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    params = {
        "client_id": client_id,
        "redirect_uri": _callback_url(request),
        "response_type": "code",
        "scope": _SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    db_client = get_supabase_client()
    if db_client:
        biz_res = await async_execute(
            db_client.table("businesses").select("settings").eq("id", user["business_id"]).limit(1)
        )
        rows = getattr(biz_res, "data", None) or []
        if rows:
            settings = rows[0].get("settings") or {}
            settings["_google_oauth_state"] = state
            settings["_google_oauth_verifier"] = code_verifier
            await async_execute(
                db_client.table("businesses").update({"settings": settings}).eq("id", user["business_id"])
            )

    return {"auth_url": f"{_GOOGLE_AUTH_URL}?{urlencode(params)}", "state": state}


@router.get("/google-calendar/callback", name="google_calendar_callback")
async def google_calendar_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(""),
) -> dict:
    client_id, client_secret = _get_google_creds()

    db_client = get_supabase_client()
    if not db_client:
        raise HTTPException(status_code=503, detail="Database unavailable")

    biz_res = await async_execute(
        db_client.table("businesses")
        .select("id, settings")
        .filter("settings->>_google_oauth_state", "eq", state)
        .limit(1)
    )
    rows = getattr(biz_res, "data", None) or []
    if not rows:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    business = rows[0]
    business_id = business["id"]
    settings = business.get("settings") or {}
    code_verifier = settings.pop("_google_oauth_verifier", "")
    settings.pop("_google_oauth_state", None)

    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _callback_url(request),
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if code_verifier:
        token_payload["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(_GOOGLE_TOKEN_URL, data=token_payload)

    if resp.status_code != 200:
        logger.error("Google token exchange failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(status_code=502, detail="Token exchange failed")

    token_data = resp.json()
    expires_in = token_data.get("expires_in", 3600)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    email = ""
    id_token_raw = token_data.get("id_token")
    if not email:
        async with httpx.AsyncClient(timeout=10) as http:
            info_resp = await http.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            if info_resp.status_code == 200:
                email = info_resp.json().get("email", "")

    settings["google_calendar"] = {
        "connected": True,
        "calendar_id": "primary",
        "refresh_token_encrypted": token_data.get("refresh_token", ""),
        "access_token": token_data["access_token"],
        "token_expires_at": expires_at,
        "email": email,
    }

    await async_execute(
        db_client.table("businesses").update({"settings": settings}).eq("id", business_id)
    )

    return {"connected": True, "calendar_email": email, "business_id": business_id}


@router.get("/google-calendar/status")
async def google_calendar_status(user: dict = Depends(get_business_user)) -> dict:
    db_client = get_supabase_client()
    if not db_client:
        raise HTTPException(status_code=503, detail="Database unavailable")
    res = await async_execute(
        db_client.table("businesses").select("settings").eq("id", user["business_id"]).limit(1)
    )
    rows = getattr(res, "data", None) or []
    if not rows:
        return {"connected": False}
    gcal = (rows[0].get("settings") or {}).get("google_calendar") or {}
    return {
        "connected": gcal.get("connected", False),
        "email": gcal.get("email"),
        "calendar_id": gcal.get("calendar_id"),
    }


@router.delete("/google-calendar")
async def google_calendar_disconnect(user: dict = Depends(get_business_user)) -> dict:
    db_client = get_supabase_client()
    if not db_client:
        raise HTTPException(status_code=503, detail="Database unavailable")

    res = await async_execute(
        db_client.table("businesses").select("settings").eq("id", user["business_id"]).limit(1)
    )
    rows = getattr(res, "data", None) or []
    if not rows:
        raise HTTPException(status_code=404, detail="Business not found")

    settings = rows[0].get("settings") or {}
    gcal = settings.get("google_calendar") or {}
    access_token = gcal.get("access_token")

    if access_token:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                await http.post(_GOOGLE_REVOKE_URL, params={"token": access_token})
        except Exception:
            logger.warning("Token revocation failed", exc_info=True)

    settings.pop("google_calendar", None)
    await async_execute(
        db_client.table("businesses").update({"settings": settings}).eq("id", user["business_id"])
    )

    return {"disconnected": True}
