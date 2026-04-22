# Phase 4 Checklist — Booking or Callback

**Goal**: When a business has calendar integration, autopilot books qualified appointments within rules. When booking isn't possible, the system falls back to a callback task with all context.

**Depends on**: Phase 3 complete (leads lifecycle, intake fields, services, follow-up scheduler fully working).

**Exit criteria**: A real inbound "Can you come Thursday?" schedules into the owner's Google Calendar without touching a human, or falls back to a callback task with all context.

---

## Definition of done

- [ ] Google Calendar OAuth flow: operator connects their calendar via `/settings/integrations`
- [ ] `booking_worker` checks real availability and creates confirmed/tentative bookings
- [ ] Confirmation SMS sent to customer with date, time, and service
- [ ] When booking fails policy checks → `tasks.type = 'callback'` created with conversation summary
- [ ] `bookings` table rows track full lifecycle: `requested → tentative → confirmed → completed | cancelled | no_show`
- [ ] Follow-up scheduler sends appointment reminder 24h before scheduled_start
- [ ] Cancellation/reschedule handling: customer texts "cancel" or "reschedule" → conversation re-classified → appropriate action
- [ ] `metric_snapshots.bookings` count is accurate
- [ ] Smoke test: `scripts/smoke_phase4.sh` simulates booking request, asserts calendar event + confirmation SMS

---

## Order of operations

### 1. Google Calendar OAuth

- [x] Port from `SMB-MetaPattern/apps/api-gateway/app/oauth_refresh.py`:
  - OAuth 2.0 authorization code flow with PKCE
  - Token storage in `businesses.settings.google_calendar` (encrypted refresh token)
  - Auto-refresh on access-token expiry
  - Scopes: `https://www.googleapis.com/auth/calendar.events`, `https://www.googleapis.com/auth/calendar.readonly`
- [x] New API routes in `apps/api/app/routes/integrations.py`:
  - `GET /v1/integrations/google-calendar/auth-url` — returns the OAuth redirect URL
  - `GET /v1/integrations/google-calendar/callback` — handles the OAuth callback, stores tokens
  - `GET /v1/integrations/google-calendar/status` — returns whether connected + which calendar is selected
  - `DELETE /v1/integrations/google-calendar` — disconnects (revokes token, clears from settings)
- [x] Store in `businesses.settings`:
  ```json
  {
    "google_calendar": {
      "connected": true,
      "calendar_id": "primary",
      "refresh_token_encrypted": "...",
      "access_token": "...",
      "token_expires_at": "...",
      "email": "owner@business.com"
    }
  }
  ```
- [x] `lib/google_calendar.py` in workers:
  - `get_availability(business_id, date_range)` → returns free/busy blocks
  - `create_event(business_id, summary, start, end, attendees, description)` → returns event ID
  - `update_event(business_id, event_id, updates)` → reschedule
  - `cancel_event(business_id, event_id)` → cancel
  - Handles token refresh transparently

### 2. Booking worker

- [x] New worker: `booking_worker.py`, consumes `booking-sync` queue
- [x] Register in `main.py` worker registry
- [ ] Job payload:
  ```json
  {
    "lead_id": "uuid",
    "conversation_id": "uuid",
    "business_id": "uuid",
    "contact_id": "uuid",
    "service_id": "uuid|null",
    "preferred_time": "2026-05-01T10:00:00-04:00 | morning | afternoon | Thursday | null",
    "trace_id": "..."
  }
  ```
- [x] Booking flow:
  1. **Verify booking is allowed** (all must pass):
     - `businesses.settings.booking_automation_enabled = true`
     - Service is in-scope (`service_id` maps to an active service)
     - Required intake fields are complete for this lead
     - Google Calendar is connected for this business
  2. **Resolve preferred time**:
     - If `preferred_time` is a specific ISO datetime → check availability at that slot
     - If fuzzy ("Thursday", "morning") → resolve to next matching slot from calendar availability
     - If null → offer the next 2–3 available slots via outbound SMS
  3. **Check availability** via `lib/google_calendar.get_availability()`
  4. **If slot is available + customer has confirmed**:
     - Insert `bookings` row: `status = 'confirmed'`, `scheduled_start`, `scheduled_end`, `assignee_user_id` (owner or first operator)
     - Create Google Calendar event via `lib/google_calendar.create_event()`
     - Store `external_calendar_event_id` on the `bookings` row
     - Advance `leads.stage` to `booked`
     - Enqueue `outbound-actions` with confirmation template
     - Enqueue `follow-up-scheduler` for 24h-before reminder
     - Emit `booking.created` event
  5. **If slot is available but customer hasn't confirmed** (fuzzy time request):
     - Insert `bookings` row with `status = 'tentative'`
     - Enqueue `outbound-actions` offering the slot: "We have [time] available. Would that work for you?"
     - Wait for customer reply (handled by normal `inbound_normalizer` → `conversation_intelligence` loop)
     - Intelligence worker detects `intent = booking_request` with acceptance → re-enqueue `booking-sync` with confirmed time
  6. **If no availability or booking not allowed** → callback fallback

### 3. Callback fallback

- [x] When booking fails any policy check or no availability:
  - Create `tasks` row: `type = 'callback'`, `priority` based on urgency, `source_table = 'leads'`, `source_id = lead.id`
  - Task description includes: customer name, phone, requested service, preferred time, urgency, conversation summary
  - Enqueue `outbound-actions` with `callback_scheduling` template: "Got it. What's the best number and a good time for the team to call you back?"
  - Set conversation `status = 'awaiting_human'`
  - Emit `handoff.created` event with `reason = 'booking_unavailable'` or `reason = 'booking_not_enabled'`

### 4. Slot offering — multi-turn booking

- [ ] Extend `conversation_intelligence` to handle `booking_request` intent:
  - If `preferred_time` is missing from `fields_collected` → `recommended_next_action = 'ask_question'`, question: "When would work best for you?"
  - If `preferred_time` is fuzzy → `recommended_next_action = 'book'`, pass the fuzzy time to `booking-sync`
  - If customer confirms a specific offered slot → `recommended_next_action = 'book'`, pass confirmed datetime
  - If customer rejects all offered slots → `recommended_next_action = 'schedule_callback'`
- [ ] `outbound_action` handles `action = 'offer_slots'`:
  - Payload includes `slots: [{start, end, display_text}, ...]`
  - Renders: "Here are some available times:\n1. [slot 1]\n2. [slot 2]\n3. [slot 3]\nWhich works best, or tell me a different time?"

### 5. Appointment reminders

- [x] After `booking.created`:
  - `follow-up-scheduler` enqueues a reminder job with `available_at = scheduled_start - 24h`
  - `followup_type = 'appointment_reminder'`
- [ ] Reminder logic:
  - Check: booking still confirmed (not cancelled/rescheduled)
  - Send via `outbound-actions` with template: "Just a reminder: you're scheduled for [service] with [business] at [time] tomorrow. Reply if you need to reschedule."
  - Emit `followup.sent` event

### 6. Cancellation + reschedule handling

- [ ] In `conversation_intelligence`, detect `intent = reschedule`:
  - Find the active booking for this contact + business
  - If customer wants to cancel: advance booking to `cancelled`, cancel Google Calendar event
  - If customer wants to reschedule: advance booking to `cancelled`, re-enter booking flow with new preferred time
  - Emit `booking.cancelled` or `booking.rescheduled` event
- [ ] No-show tracking: if no `booking.completed` event fires within 1 hour after `scheduled_end`, an async job (via follow-up-scheduler) marks the booking as `no_show` and creates a follow-up task

### 7. Conversation intelligence — booking routing

- [x] When `recommended_next_action = 'book'`:
  - Check if `businesses.settings.booking_automation_enabled`
  - If yes → enqueue `booking-sync`
  - If no → treat as `schedule_callback` → enqueue `human-handoff` with `task_type = 'callback'`
- [ ] When `recommended_next_action = 'schedule_callback'`:
  - Enqueue `human-handoff` directly
  - Send `callback_scheduling` template

### 8. Metrics additions

- [ ] `metric_snapshots.bookings` — count of `booking.created` events per day
- [ ] `metric_snapshots.payload.booking_requests` — count of `booking.requested` events
- [ ] `metric_snapshots.payload.callbacks_created` — count of `tasks.type = 'callback'` per day
- [ ] `metric_snapshots.payload.no_shows` — count of bookings marked no_show

### 9. Smoke test

- [ ] `scripts/smoke_phase4.sh`:
  1. Seed a business with `booking_automation_enabled = true` (note: without real Google Calendar, the worker will fall back to callback)
  2. Simulate inbound: "Can you come Thursday morning?"
  3. Assert: intelligence classifies as `booking_request` with preferred_time extracted
  4. Assert: either `bookings` row created (if calendar mocked) or `tasks.type = 'callback'` created
  5. Assert: customer received a confirmation or callback-scheduling message
  6. If booking was created: simulate a 24h-later check for reminder job

---

## Out of scope for Phase 4

- Non-Google calendar providers (Outlook, Apple, Calendly) — future integration
- Multi-tech / multi-crew scheduling (assign specific technician to slot)
- Payment/deposit collection at booking time
- In-app booking widget (web form → booking flow)
- Recurring appointment scheduling

---

## Risks to watch

1. **OAuth token expiry** — Google refresh tokens can be revoked by the user at any time. The worker must handle `401 Unauthorized` from the Calendar API gracefully (fall back to callback + alert operator).
2. **Timezone hell** — customer says "Thursday morning" but business is in a different timezone. The intelligence worker must resolve relative times against `businesses.timezone`, not UTC.
3. **Overbooking** — two simultaneous booking requests for the same slot. Mitigate with calendar re-check immediately before `create_event`; if the slot is no longer free, offer alternatives.
4. **No-show false positives** — the 1-hour-after-end check may fire for legitimate long appointments. Make the grace period configurable per-service.

---

## SMB-MetaPattern components to port

| Source | Destination | Notes |
|---|---|---|
| `apps/api-gateway/app/oauth_refresh.py` (326 L) | `apps/api/app/services/google_oauth.py` | Strip real-estate-specific scopes; keep token refresh + encrypted storage pattern |
| `apps/api-gateway/app/services/calendar_service.py` (if exists) | `apps/workers/src/lib/google_calendar.py` | Reference for availability + event creation patterns |
