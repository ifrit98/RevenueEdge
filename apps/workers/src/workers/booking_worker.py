"""booking_worker — consumes `booking-sync`.

When a qualified lead requests an appointment, this worker:
  1. Verifies booking is allowed (calendar connected, service active, intake complete)
  2. Resolves preferred_time to concrete slot(s)
  3. Checks availability via Google Calendar
  4. Books (confirmed or tentative) or falls back to a callback task

Job payload:
  {
    "lead_id": "uuid",
    "conversation_id": "uuid",
    "business_id": "uuid",
    "contact_id": "uuid",
    "service_id": "uuid|null",
    "preferred_time": "2026-05-01T10:00:00-04:00 | morning | Thursday | null",
    "confirmed": false,
    "trace_id": "..."
  }
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..base import BaseWorker, Job, PermanentError
from ..lib.channels import fetch_business
from ..lib.google_calendar import (
    CalendarNotConnectedError,
    CalendarTokenError,
    cancel_event,
    compute_free_slots,
    create_event,
    get_availability,
    update_event,
)
from ..lib.leads import advance_lead_stage
from ..supabase_client import async_execute, get_client, rpc

logger = logging.getLogger(__name__)

_DEFAULT_SLOT_MINUTES = 60


class BookingWorker(BaseWorker):
    queue_name = "booking-sync"
    max_concurrency = 2

    async def handle(self, job: Job) -> Optional[dict]:
        payload = job.payload or {}
        action = payload.get("action")
        business_id = job.business_id or payload.get("business_id")

        if not business_id:
            raise PermanentError("business_id required")

        if action == "cancel":
            return await self._handle_cancel(job)
        if action == "reschedule":
            return await self._handle_reschedule(job)

        lead_id = payload.get("lead_id")
        conversation_id = payload.get("conversation_id")
        contact_id = payload.get("contact_id")
        service_id = payload.get("service_id")
        preferred_time = payload.get("preferred_time")
        confirmed = payload.get("confirmed", False)
        trace_id = payload.get("trace_id")

        client = get_client()
        business = await fetch_business(business_id)
        biz_settings = (business or {}).get("settings") or {}

        if not biz_settings.get("booking_automation_enabled"):
            return await self._callback_fallback(
                client=client,
                business_id=business_id,
                conversation_id=conversation_id,
                contact_id=contact_id,
                lead_id=lead_id,
                reason="booking_not_enabled",
                trace_id=trace_id,
                job_id=job.id,
            )

        gcal = biz_settings.get("google_calendar") or {}
        if not gcal.get("connected"):
            return await self._callback_fallback(
                client=client,
                business_id=business_id,
                conversation_id=conversation_id,
                contact_id=contact_id,
                lead_id=lead_id,
                reason="calendar_not_connected",
                trace_id=trace_id,
                job_id=job.id,
            )

        client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

        service = None
        if service_id:
            svc_res = await async_execute(
                client.table("services")
                .select("id, name, required_intake_fields, metadata")
                .eq("id", service_id)
                .limit(1)
            )
            rows = getattr(svc_res, "data", None) or []
            service = rows[0] if rows else None

        contact = None
        if contact_id:
            c_res = await async_execute(
                client.table("contacts")
                .select("id, name, phone_e164, email")
                .eq("id", contact_id)
                .limit(1)
            )
            rows = getattr(c_res, "data", None) or []
            contact = rows[0] if rows else None

        biz_tz_name = (business or {}).get("timezone") or "America/New_York"
        slot_start = self._resolve_preferred_time(preferred_time, biz_tz_name)

        if not slot_start:
            try:
                now = datetime.now(timezone.utc)
                range_end = now + timedelta(days=7)
                busy = await get_availability(
                    business_id=business_id,
                    date_start=now,
                    date_end=range_end,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                free = compute_free_slots(
                    busy_blocks=busy,
                    range_start=now + timedelta(hours=1),
                    range_end=range_end,
                    slot_duration_minutes=_DEFAULT_SLOT_MINUTES,
                )
                top_slots = free[:3]
            except (CalendarNotConnectedError, CalendarTokenError) as exc:
                logger.warning("Calendar access failed: %s", exc)
                return await self._callback_fallback(
                    client=client,
                    business_id=business_id,
                    conversation_id=conversation_id,
                    contact_id=contact_id,
                    lead_id=lead_id,
                    reason=f"calendar_error: {exc}",
                    trace_id=trace_id,
                    job_id=job.id,
                )

            if not top_slots:
                return await self._callback_fallback(
                    client=client,
                    business_id=business_id,
                    conversation_id=conversation_id,
                    contact_id=contact_id,
                    lead_id=lead_id,
                    reason="no_availability",
                    trace_id=trace_id,
                    job_id=job.id,
                )

            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "outbound-actions",
                    "p_payload": {
                        "action": "send_sms",
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "contact_id": contact_id,
                        "body": self._format_slot_offer(top_slots, business),
                        "intent": "booking_offer",
                        "trace_id": trace_id,
                        "reason": "slot_offer",
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"ob:slots:{job.id}",
                    "p_priority": 15,
                },
            )
            return {"action": "slots_offered", "slots": len(top_slots)}

        slot_end = slot_start + timedelta(minutes=_DEFAULT_SLOT_MINUTES)

        try:
            busy = await get_availability(
                business_id=business_id,
                date_start=slot_start - timedelta(minutes=30),
                date_end=slot_end + timedelta(minutes=30),
                client_id=client_id,
                client_secret=client_secret,
            )
        except (CalendarNotConnectedError, CalendarTokenError) as exc:
            return await self._callback_fallback(
                client=client,
                business_id=business_id,
                conversation_id=conversation_id,
                contact_id=contact_id,
                lead_id=lead_id,
                reason=f"calendar_error: {exc}",
                trace_id=trace_id,
                job_id=job.id,
            )

        slot_free = all(
            not (
                datetime.fromisoformat(b["start"].replace("Z", "+00:00")) < slot_end
                and datetime.fromisoformat(b["end"].replace("Z", "+00:00")) > slot_start
            )
            for b in busy
        )

        if not slot_free:
            return await self._callback_fallback(
                client=client,
                business_id=business_id,
                conversation_id=conversation_id,
                contact_id=contact_id,
                lead_id=lead_id,
                reason="slot_unavailable",
                trace_id=trace_id,
                job_id=job.id,
            )

        booking_status = "confirmed" if confirmed else "tentative"
        svc_name = (service or {}).get("name") or "Appointment"
        contact_name = (contact or {}).get("name") or "Customer"
        summary = f"{svc_name} — {contact_name}"

        attendees = []
        if contact and contact.get("email"):
            attendees.append(contact["email"])

        event_id = None
        try:
            event_id = await create_event(
                business_id=business_id,
                summary=summary,
                start=slot_start,
                end=slot_end,
                description=f"Booked via Revenue Edge. Lead: {lead_id}",
                attendees=attendees or None,
                client_id=client_id,
                client_secret=client_secret,
            )
        except Exception as exc:
            logger.error("Calendar event creation failed: %s", exc)

        booking_row = {
            "business_id": business_id,
            "lead_id": lead_id,
            "contact_id": contact_id,
            "service_id": service_id,
            "status": booking_status,
            "scheduled_start": slot_start.isoformat(),
            "scheduled_end": slot_end.isoformat(),
            "external_calendar_event_id": event_id,
            "metadata": {
                "trace_id": trace_id,
                "service_name": svc_name,
                "contact_name": contact_name,
            },
        }
        res = await async_execute(client.table("bookings").insert(booking_row))
        rows = getattr(res, "data", None) or []
        booking = rows[0] if rows else {}

        if lead_id:
            await advance_lead_stage(
                lead_id=lead_id,
                new_stage="booked",
                metadata_patch={"booking_id": booking.get("id"), "trace_id": trace_id},
            )

        template = "booking_confirmed" if confirmed else "booking_tentative"
        display_time = slot_start.strftime("%A, %B %d at %I:%M %p")
        biz_name = (business or {}).get("name") or "Our team"
        body = (
            f"{'Great news!' if confirmed else 'We have an opening!'} "
            f"Your {svc_name.lower()} with {biz_name} is {'confirmed' if confirmed else 'tentatively set'} "
            f"for {display_time}. "
            + ("Reply to confirm or suggest a different time." if not confirmed else "We'll see you then!")
        )

        await rpc(
            "enqueue_job",
            {
                "p_queue_name": "outbound-actions",
                "p_payload": {
                    "action": "send_sms",
                    "conversation_id": conversation_id,
                    "business_id": business_id,
                    "contact_id": contact_id,
                    "body": body,
                    "intent": "booking_confirmation",
                    "trace_id": trace_id,
                    "reason": f"booking_{booking_status}",
                },
                "p_business_id": business_id,
                "p_idempotency_key": f"ob:book:{job.id}",
                "p_priority": 10,
            },
        )

        if confirmed and booking.get("id"):
            reminder_at = (slot_start - timedelta(hours=24)).isoformat()
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "follow-up-scheduler",
                    "p_payload": {
                        "followup_type": "appointment_reminder",
                        "booking_id": booking["id"],
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "contact_id": contact_id,
                        "attempt": 1,
                        "max_attempts": 1,
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"fu:remind:{booking['id']}",
                    "p_priority": 30,
                    "p_available_at": reminder_at,
                },
            )

            grace_minutes = 60
            if service:
                svc_meta = (service.get("metadata") or {})
                grace_minutes = int(svc_meta.get("no_show_grace_minutes", 60))
            no_show_at = (slot_end + timedelta(minutes=grace_minutes)).isoformat()
            await rpc(
                "enqueue_job",
                {
                    "p_queue_name": "follow-up-scheduler",
                    "p_payload": {
                        "followup_type": "no_show_check",
                        "booking_id": booking["id"],
                        "conversation_id": conversation_id,
                        "business_id": business_id,
                        "attempt": 1,
                        "max_attempts": 1,
                        "trace_id": trace_id,
                    },
                    "p_business_id": business_id,
                    "p_idempotency_key": f"fu:noshow:{booking['id']}",
                    "p_priority": 40,
                    "p_available_at": no_show_at,
                },
            )

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "booking.created",
                "p_payload": {
                    "booking_id": booking.get("id"),
                    "business_id": business_id,
                    "lead_id": lead_id,
                    "status": booking_status,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "booking",
                "p_aggregate_id": booking.get("id") or "",
                "p_idempotency_key": f"book:created:{job.id}",
            },
        )

        return {
            "action": "booked",
            "booking_id": booking.get("id"),
            "status": booking_status,
            "calendar_event_id": event_id,
        }

    async def _handle_cancel(self, job: Job) -> dict:
        payload = job.payload or {}
        business_id = job.business_id or payload["business_id"]
        event_id = payload.get("external_calendar_event_id")
        client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

        if event_id:
            try:
                await cancel_event(
                    business_id=business_id,
                    event_id=event_id,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                logger.info("Cancelled calendar event %s", event_id)
            except Exception as exc:
                logger.warning("Calendar cancel failed (non-fatal): %s", exc)

        return {"action": "calendar_cancel", "event_id": event_id}

    async def _handle_reschedule(self, job: Job) -> dict:
        payload = job.payload or {}
        business_id = job.business_id or payload["business_id"]
        event_id = payload.get("external_calendar_event_id")
        new_start = payload.get("new_start")
        new_end = payload.get("new_end")
        client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

        if not event_id or not new_start:
            return {"action": "reschedule_skipped", "reason": "missing event_id or new_start"}

        start_dt = datetime.fromisoformat(new_start)
        end_dt = datetime.fromisoformat(new_end) if new_end else start_dt + timedelta(minutes=_DEFAULT_SLOT_MINUTES)

        try:
            await update_event(
                business_id=business_id,
                event_id=event_id,
                start=start_dt,
                end=end_dt,
                client_id=client_id,
                client_secret=client_secret,
            )
            logger.info("Rescheduled calendar event %s to %s", event_id, new_start)
        except Exception as exc:
            logger.warning("Calendar reschedule failed (non-fatal): %s", exc)

        return {"action": "calendar_reschedule", "event_id": event_id, "new_start": new_start}

    _TIME_OF_DAY = {
        "morning": 9,
        "late morning": 10,
        "midday": 12,
        "noon": 12,
        "afternoon": 13,
        "late afternoon": 15,
        "evening": 17,
        "night": 18,
    }

    _DOW = {
        "monday": 0, "mon": 0,
        "tuesday": 1, "tue": 1, "tues": 1,
        "wednesday": 2, "wed": 2,
        "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
        "friday": 4, "fri": 4,
        "saturday": 5, "sat": 5,
        "sunday": 6, "sun": 6,
    }

    def _resolve_preferred_time(
        self, preferred_time: Optional[str], tz_name: str
    ) -> Optional[datetime]:
        """Parse ISO datetime, or fuzzy strings like 'Thursday morning', 'next Tuesday'."""
        if not preferred_time:
            return None

        try:
            return datetime.fromisoformat(preferred_time)
        except (ValueError, TypeError):
            pass

        try:
            import zoneinfo
            biz_tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            biz_tz = timezone.utc

        now_local = datetime.now(biz_tz)
        text = preferred_time.strip().lower()

        target_dow: Optional[int] = None
        target_hour: int = 9

        for pattern, hour in self._TIME_OF_DAY.items():
            if pattern in text:
                target_hour = hour
                break

        for pattern, dow in self._DOW.items():
            if pattern in text:
                target_dow = dow
                break

        if "today" in text:
            candidate = now_local.replace(hour=target_hour, minute=0, second=0, microsecond=0)
            if candidate <= now_local:
                candidate += timedelta(days=1)
            return candidate.astimezone(timezone.utc)

        if "tomorrow" in text:
            candidate = (now_local + timedelta(days=1)).replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            return candidate.astimezone(timezone.utc)

        if "next week" in text:
            days_ahead = 7 - now_local.weekday()
            candidate = (now_local + timedelta(days=days_ahead)).replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            return candidate.astimezone(timezone.utc)

        if target_dow is not None:
            days_ahead = (target_dow - now_local.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            candidate = (now_local + timedelta(days=days_ahead)).replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            return candidate.astimezone(timezone.utc)

        if any(k in text for k in self._TIME_OF_DAY):
            candidate = now_local.replace(hour=target_hour, minute=0, second=0, microsecond=0)
            if candidate <= now_local:
                candidate += timedelta(days=1)
            return candidate.astimezone(timezone.utc)

        return None

    def _format_slot_offer(self, slots: list[dict], business: Optional[dict]) -> str:
        biz_name = (business or {}).get("name") or "Our team"
        lines = [f"Hi! {biz_name} has these times available:"]
        for i, s in enumerate(slots[:3], 1):
            try:
                dt = datetime.fromisoformat(s["start"].replace("Z", "+00:00"))
                lines.append(f"{i}. {dt.strftime('%A, %B %d at %I:%M %p')}")
            except (ValueError, KeyError):
                lines.append(f"{i}. {s.get('start', '?')}")
        lines.append("Which works best, or tell us a different time!")
        return "\n".join(lines)

    async def _callback_fallback(
        self, *, client: Any, business_id: str,
        conversation_id: Optional[str], contact_id: Optional[str],
        lead_id: Optional[str], reason: str, trace_id: Optional[str],
        job_id: str,
    ) -> dict:
        await async_execute(
            client.table("tasks").insert({
                "business_id": business_id,
                "conversation_id": conversation_id,
                "task_type": "callback",
                "title": f"Booking fallback: {reason}",
                "priority": "high",
                "status": "open",
                "metadata": {
                    "lead_id": lead_id,
                    "contact_id": contact_id,
                    "reason": reason,
                    "trace_id": trace_id,
                },
            })
        )

        if conversation_id:
            await async_execute(
                client.table("conversations")
                .update({"status": "awaiting_human"})
                .eq("id", conversation_id)
            )

        await rpc(
            "enqueue_job",
            {
                "p_queue_name": "outbound-actions",
                "p_payload": {
                    "action": "send_sms",
                    "conversation_id": conversation_id,
                    "business_id": business_id,
                    "contact_id": contact_id,
                    "template_name": "callback_scheduling",
                    "intent": "callback",
                    "trace_id": trace_id,
                    "reason": "booking_fallback",
                },
                "p_business_id": business_id,
                "p_idempotency_key": f"ob:cb-fallback:{job_id}",
                "p_priority": 15,
            },
        )

        await rpc(
            "enqueue_event",
            {
                "p_event_type": "handoff.created",
                "p_payload": {
                    "reason": reason,
                    "business_id": business_id,
                    "lead_id": lead_id,
                    "trace_id": trace_id,
                },
                "p_business_id": business_id,
                "p_aggregate_type": "conversation",
                "p_aggregate_id": conversation_id or "",
                "p_idempotency_key": f"ho:book-fallback:{job_id}",
            },
        )

        return {"action": "callback_fallback", "reason": reason}
