"""/v1/bookings — list, detail, cancel, reschedule, complete.

Bookings are created by the booking_worker, not directly via API.
The operator uses these endpoints to manage existing bookings.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_business_user
from ..db import async_execute, get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/bookings", tags=["bookings"])


class BookingUpdate(BaseModel):
    notes: Optional[str] = None
    assignee_user_id: Optional[str] = None


class BookingReschedule(BaseModel):
    new_start: str = Field(..., description="ISO 8601 datetime for the new start")
    new_end: Optional[str] = Field(None, description="ISO 8601 datetime for the new end")


@router.get("")
async def list_bookings(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    q = (
        client.table("bookings")
        .select(
            "id, lead_id, contact_id, service_id, status, "
            "scheduled_start, scheduled_end, assignee_user_id, "
            "external_calendar_event_id, metadata, created_at, updated_at",
            count="exact",
        )
        .eq("business_id", user["business_id"])
    )
    if status:
        q = q.eq("status", status)
    q = q.order("scheduled_start", desc=False).range(offset, offset + limit - 1)
    res = await async_execute(q)
    return {
        "bookings": res.data or [],
        "total": getattr(res, "count", None) or len(res.data or []),
    }


@router.get("/{booking_id}")
async def get_booking(booking_id: str, user: dict = Depends(get_business_user)) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    res = await async_execute(
        client.table("bookings")
        .select("*")
        .eq("id", booking_id)
        .eq("business_id", user["business_id"])
        .limit(1)
    )
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Booking not found")
    booking = res.data[0]
    if booking.get("contact_id"):
        c_res = await async_execute(
            client.table("contacts")
            .select("id, name, phone_e164, email")
            .eq("id", booking["contact_id"])
            .limit(1)
        )
        booking["contact"] = (c_res.data or [None])[0]
    if booking.get("service_id"):
        s_res = await async_execute(
            client.table("services")
            .select("id, name, description")
            .eq("id", booking["service_id"])
            .limit(1)
        )
        booking["service"] = (s_res.data or [None])[0]
    return booking


@router.patch("/{booking_id}")
async def update_booking(
    booking_id: str,
    body: BookingUpdate,
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
        client.table("bookings")
        .update(patch)
        .eq("id", booking_id)
        .eq("business_id", user["business_id"])
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Booking not found")
    return rows[0]


@router.post("/{booking_id}/cancel")
async def cancel_booking(
    booking_id: str,
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    res = await async_execute(
        client.table("bookings")
        .select("id, business_id, status, external_calendar_event_id")
        .eq("id", booking_id)
        .eq("business_id", user["business_id"])
        .limit(1)
    )
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Booking not found")
    booking = res.data[0]

    if booking["status"] in {"cancelled", "completed", "no_show"}:
        raise HTTPException(status_code=409, detail=f"Booking is already {booking['status']}")

    now = datetime.now(timezone.utc).isoformat()
    await async_execute(
        client.table("bookings")
        .update({"status": "cancelled", "updated_at": now})
        .eq("id", booking_id)
    )

    event_id = booking.get("external_calendar_event_id")
    if event_id:
        await async_execute(
            client.rpc(
                "enqueue_job",
                {
                    "p_queue_name": "booking-sync",
                    "p_payload": {
                        "action": "cancel",
                        "booking_id": booking_id,
                        "business_id": user["business_id"],
                        "external_calendar_event_id": event_id,
                    },
                    "p_business_id": user["business_id"],
                    "p_idempotency_key": f"bksync:cancel:{booking_id}",
                    "p_priority": 15,
                },
            )
        )

    await async_execute(
        client.rpc(
            "enqueue_event",
            {
                "p_event_type": "booking.cancelled",
                "p_payload": {
                    "booking_id": booking_id,
                    "business_id": user["business_id"],
                },
                "p_business_id": user["business_id"],
                "p_aggregate_type": "booking",
                "p_aggregate_id": booking_id,
                "p_idempotency_key": f"book:cancel:{booking_id}",
            },
        )
    )

    return {"cancelled": True, "booking_id": booking_id}


@router.post("/{booking_id}/complete")
async def complete_booking(
    booking_id: str,
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    now = datetime.now(timezone.utc).isoformat()
    res = await async_execute(
        client.table("bookings")
        .update({"status": "completed", "updated_at": now})
        .eq("id", booking_id)
        .eq("business_id", user["business_id"])
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Booking not found")

    await async_execute(
        client.rpc(
            "enqueue_event",
            {
                "p_event_type": "booking.completed",
                "p_payload": {"booking_id": booking_id, "business_id": user["business_id"]},
                "p_business_id": user["business_id"],
                "p_aggregate_type": "booking",
                "p_aggregate_id": booking_id,
                "p_idempotency_key": f"book:complete:{booking_id}",
            },
        )
    )

    return {"completed": True, "booking_id": booking_id}


@router.post("/{booking_id}/reschedule")
async def reschedule_booking(
    booking_id: str,
    body: BookingReschedule,
    user: dict = Depends(get_business_user),
) -> dict:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    now = datetime.now(timezone.utc).isoformat()
    patch: dict = {
        "scheduled_start": body.new_start,
        "status": "tentative",
        "updated_at": now,
    }
    if body.new_end:
        patch["scheduled_end"] = body.new_end

    res = await async_execute(
        client.table("bookings")
        .update(patch)
        .eq("id", booking_id)
        .eq("business_id", user["business_id"])
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Booking not found")

    booking = rows[0]
    event_id = booking.get("external_calendar_event_id")
    if event_id:
        await async_execute(
            client.rpc(
                "enqueue_job",
                {
                    "p_queue_name": "booking-sync",
                    "p_payload": {
                        "action": "reschedule",
                        "booking_id": booking_id,
                        "business_id": user["business_id"],
                        "external_calendar_event_id": event_id,
                        "new_start": body.new_start,
                        "new_end": body.new_end,
                    },
                    "p_business_id": user["business_id"],
                    "p_idempotency_key": f"bksync:resched:{booking_id}:{now}",
                    "p_priority": 15,
                },
            )
        )

    await async_execute(
        client.rpc(
            "enqueue_event",
            {
                "p_event_type": "booking.rescheduled",
                "p_payload": {
                    "booking_id": booking_id,
                    "business_id": user["business_id"],
                    "new_start": body.new_start,
                },
                "p_business_id": user["business_id"],
                "p_aggregate_type": "booking",
                "p_aggregate_id": booking_id,
                "p_idempotency_key": f"book:resched:{booking_id}:{now}",
            },
        )
    )

    return {"rescheduled": True, "booking_id": booking_id}
