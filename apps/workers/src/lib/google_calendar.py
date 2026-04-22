"""Google Calendar integration helpers.

Token storage lives in ``businesses.settings.google_calendar``:
  {
    "connected": true,
    "calendar_id": "primary",
    "refresh_token_encrypted": "...",
    "access_token": "...",
    "token_expires_at": "2026-05-01T12:00:00Z",
    "email": "owner@business.com"
  }

All calendar operations transparently refresh the OAuth token when expired.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from ..supabase_client import async_execute, get_client

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
_FREEBUSY_URL = f"{_CALENDAR_BASE}/freeBusy"


class CalendarNotConnectedError(Exception):
    pass


class CalendarTokenError(Exception):
    pass


class CalendarUnavailableError(Exception):
    """Raised when Calendar API returns a non-200 or network error.

    Callers should treat availability as *unknown* (not "fully free").
    """
    pass


async def _get_calendar_config(business_id: str) -> dict:
    client = get_client()
    res = await async_execute(
        client.table("businesses")
        .select("settings")
        .eq("id", business_id)
        .limit(1)
    )
    rows = getattr(res, "data", None) or []
    if not rows:
        raise CalendarNotConnectedError(f"Business {business_id} not found")
    settings = rows[0].get("settings") or {}
    gcal = settings.get("google_calendar") or {}
    if not gcal.get("connected"):
        raise CalendarNotConnectedError("Google Calendar not connected")
    return gcal


async def _refresh_access_token(
    business_id: str,
    gcal: dict,
    client_id: str,
    client_secret: str,
) -> str:
    """Refresh the Google OAuth access token and persist it."""
    refresh_token = gcal.get("refresh_token_encrypted") or gcal.get("refresh_token")
    if not refresh_token:
        raise CalendarTokenError("No refresh token available")

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
    except httpx.RequestError as exc:
        raise CalendarUnavailableError(f"Token refresh network error: {exc}") from exc

    if resp.status_code != 200:
        logger.error("Token refresh failed: %s %s", resp.status_code, resp.text)
        raise CalendarTokenError(f"Token refresh failed: {resp.status_code}")

    token_data = resp.json()
    new_access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in", 3600)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

    client = get_client()
    res = await async_execute(
        client.table("businesses")
        .select("settings")
        .eq("id", business_id)
        .limit(1)
    )
    rows = getattr(res, "data", None) or []
    if rows:
        settings = rows[0].get("settings") or {}
        gcal_cfg = settings.get("google_calendar") or {}
        gcal_cfg["access_token"] = new_access_token
        gcal_cfg["token_expires_at"] = expires_at
        if token_data.get("refresh_token"):
            gcal_cfg["refresh_token_encrypted"] = token_data["refresh_token"]
        settings["google_calendar"] = gcal_cfg
        await async_execute(
            client.table("businesses")
            .update({"settings": settings})
            .eq("id", business_id)
        )

    return new_access_token


async def _get_access_token(
    business_id: str,
    gcal: dict,
    client_id: str,
    client_secret: str,
) -> str:
    """Return a valid access token, refreshing if expired."""
    access_token = gcal.get("access_token")
    expires_at_str = gcal.get("token_expires_at")

    if access_token and expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            if expires_at > datetime.now(timezone.utc) + timedelta(minutes=2):
                return access_token
        except (ValueError, TypeError):
            pass

    return await _refresh_access_token(business_id, gcal, client_id, client_secret)


async def get_availability(
    *,
    business_id: str,
    date_start: datetime,
    date_end: datetime,
    client_id: str = "",
    client_secret: str = "",
) -> list[dict]:
    """Return free/busy blocks from Google Calendar.

    Returns a list of ``{"start": iso, "end": iso}`` for *busy* periods.
    Free slots are the gaps between busy periods within the queried range.
    """
    gcal = await _get_calendar_config(business_id)
    calendar_id = gcal.get("calendar_id") or "primary"
    token = await _get_access_token(business_id, gcal, client_id, client_secret)

    body = {
        "timeMin": date_start.isoformat(),
        "timeMax": date_end.isoformat(),
        "timeZone": "UTC",
        "items": [{"id": calendar_id}],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                _FREEBUSY_URL,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as exc:
        logger.error("FreeBusy network error: %s", exc)
        raise CalendarUnavailableError(f"Network error: {exc}") from exc

    if resp.status_code == 401:
        token = await _refresh_access_token(business_id, gcal, client_id, client_secret)
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    _FREEBUSY_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.RequestError as exc:
            logger.error("FreeBusy network error on retry: %s", exc)
            raise CalendarUnavailableError(f"Network error: {exc}") from exc

    if resp.status_code != 200:
        logger.error("FreeBusy query failed: %s %s", resp.status_code, resp.text)
        raise CalendarUnavailableError(f"FreeBusy returned {resp.status_code}")

    data = resp.json()
    calendars = data.get("calendars") or {}
    cal_data = calendars.get(calendar_id) or {}
    return cal_data.get("busy") or []


def compute_free_slots(
    *,
    busy_blocks: list[dict],
    range_start: datetime,
    range_end: datetime,
    slot_duration_minutes: int = 60,
) -> list[dict]:
    """Derive available booking slots from busy blocks."""
    busy = sorted(
        [
            (
                datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
            )
            for b in busy_blocks
        ],
        key=lambda x: x[0],
    )

    slots: list[dict] = []
    cursor = range_start
    duration = timedelta(minutes=slot_duration_minutes)

    for busy_start, busy_end in busy:
        while cursor + duration <= busy_start:
            slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
            cursor += duration
        cursor = max(cursor, busy_end)

    while cursor + duration <= range_end:
        slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
        cursor += duration

    return slots


async def create_event(
    *,
    business_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "",
    attendees: Optional[list[str]] = None,
    client_id: str = "",
    client_secret: str = "",
) -> Optional[str]:
    """Create a Google Calendar event. Returns the event ID or None on failure."""
    gcal = await _get_calendar_config(business_id)
    calendar_id = gcal.get("calendar_id") or "primary"
    token = await _get_access_token(business_id, gcal, client_id, client_secret)

    event_body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        "description": description,
    }
    if attendees:
        event_body["attendees"] = [{"email": e} for e in attendees]

    url = f"{_CALENDAR_BASE}/calendars/{calendar_id}/events"
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                url,
                json=event_body,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as exc:
        logger.error("Create event network error: %s", exc)
        return None

    if resp.status_code == 401:
        token = await _refresh_access_token(business_id, gcal, client_id, client_secret)
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    url,
                    json=event_body,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.RequestError as exc:
            logger.error("Create event network error on retry: %s", exc)
            return None

    if resp.status_code not in (200, 201):
        logger.error("Create event failed: %s %s", resp.status_code, resp.text)
        return None

    return resp.json().get("id")


async def update_event(
    *,
    business_id: str,
    event_id: str,
    updates: dict,
    client_id: str = "",
    client_secret: str = "",
) -> bool:
    """Update an existing Google Calendar event. Returns True on success."""
    gcal = await _get_calendar_config(business_id)
    calendar_id = gcal.get("calendar_id") or "primary"
    token = await _get_access_token(business_id, gcal, client_id, client_secret)

    url = f"{_CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.patch(
                url,
                json=updates,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as exc:
        logger.error("Update event network error: %s", exc)
        return False

    if resp.status_code == 401:
        token = await _refresh_access_token(business_id, gcal, client_id, client_secret)
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.patch(
                    url,
                    json=updates,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.RequestError as exc:
            logger.error("Update event network error on retry: %s", exc)
            return False

    if resp.status_code != 200:
        logger.error("Update event failed: %s %s", resp.status_code, resp.text)
        return False
    return True


async def cancel_event(
    *,
    business_id: str,
    event_id: str,
    client_id: str = "",
    client_secret: str = "",
) -> bool:
    """Cancel (delete) a Google Calendar event. Returns True on success."""
    gcal = await _get_calendar_config(business_id)
    calendar_id = gcal.get("calendar_id") or "primary"
    token = await _get_access_token(business_id, gcal, client_id, client_secret)

    url = f"{_CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.delete(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as exc:
        logger.error("Cancel event network error: %s", exc)
        return False

    if resp.status_code == 401:
        token = await _refresh_access_token(business_id, gcal, client_id, client_secret)
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.delete(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.RequestError as exc:
            logger.error("Cancel event network error on retry: %s", exc)
            return False

    if resp.status_code not in (200, 204):
        logger.error("Cancel event failed: %s %s", resp.status_code, resp.text)
        return False
    return True
