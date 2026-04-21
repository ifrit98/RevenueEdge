# Phase 1 Checklist — Missed-Call Recovery

**Goal**: A configured business with an inbound Retell number gets a text within 60 seconds of a missed call, and the conversation reaches a human-confirmable state.

This is the first user-visible vertical slice. Everything before it is scaffolding; everything after builds on it.

---

## Definition of done

- [ ] Inbound Retell voice call where agent doesn't answer → SMS sent to the caller within 60s
- [ ] SMS reply from caller → conversation updated, intent classified, task created if handoff needed
- [ ] Dashboard inbox shows the conversation with transcript, intent, lead record, task
- [ ] `metric_snapshots` row shows `missed_calls_recovered` count
- [ ] End-to-end trace (trace_id) visible across `queue_jobs.payload`, `events`, `conversations.messages`, `tasks`
- [ ] Rate limits: no more than 1 SMS per contact per 2 minutes; no SMS to STOP'd numbers
- [ ] Smoke test passes: `scripts/smoke_phase1.sh` seeds a business, simulates a missed call, asserts task creation

---

## Order of operations

### 1. Supabase ready (depends on: Phase 0 migrations applied)

- [ ] `schema.sql` applied to a fresh Supabase project
- [ ] `seed_mvp_defaults.sql` applied (FAQ templates, vertical presets, workflow definitions)
- [ ] Seed a test business row: `insert into businesses (name, vertical, hours, timezone) …`
- [ ] Seed a test channel: `insert into channels (business_id, kind, identifier, provider, config) values (…, 'voice', '+15551234567', 'retell', '{…}')`
- [ ] Confirm RLS policies block cross-business reads: run a test query as anon + wrong business_id
- [ ] `claim_queue_jobs` RPC works: manually enqueue a dummy job, call the RPC, see it return

### 2. `re-webhooks` service receiving Retell events

- [ ] Port `apps/api-gateway/app/webhooks_retell.py` signature verification into `apps/webhooks/app/retell.py`
- [ ] Route handler: on `call.ended` or `call_analyzed`, look up `channels` by `to_number`, derive `business_id`
- [ ] Build `inbound-events` payload per pack contract:
  ```json
  {
    "event_type": "call.missed",
    "business_id": "...",
    "channel_id": "...",
    "trace_id": "...",
    "idempotency_key": "retell:<call_id>",
    "payload": {
      "from_number": "...",
      "to_number": "...",
      "started_at": "...",
      "duration_seconds": 0,
      "recording_url": null,
      "transcript": null
    }
  }
  ```
- [ ] Enqueue via `select enqueue_job('inbound-events', …)`
- [ ] Return `2xx` fast (< 500ms), even if job enqueue fails (log + emit error event)
- [ ] Integration test: POST a fake Retell webhook payload, assert row lands in `queue_jobs`

### 3. `inbound_normalizer_worker` reading `inbound-events`

- [ ] Worker scaffold (Python or TS, per chosen language) polls `claim_queue_jobs('inbound-events', worker_id, 10, 300)`
- [ ] Handler for `call.missed`:
  - [ ] Upsert `contacts` row by `phone_number`
  - [ ] Create `conversations` row (channel='voice', direction='inbound', started_at, ended_at)
  - [ ] Create `conversation_messages` row with transcript (if present) or metadata note
  - [ ] Classify intent via simple rules (any voicemail text? any after-hours flag?) → seed `leads` row with `intent='unknown'` if no match
  - [ ] Decide next action: if contact opted-out (`contact_preferences.sms_opt_out=true`), drop. Else enqueue `outbound-actions` job with `action='sms'`, template='missed_call_followup'
  - [ ] Write `events` row: `event_name='inbound.call.missed'`, link to conversation + contact
  - [ ] Call `complete_queue_job(job_id, result_jsonb)` on success
  - [ ] On failure: `fail_queue_job(job_id, err_text, retry_in_seconds)` with exponential backoff
- [ ] Idempotency check: repeat delivery of the same `retell:<call_id>` should be a no-op (use `events.idempotency_key` unique constraint)

### 4. `outbound_action_worker` sending SMS

- [ ] Port `apps/api-gateway/app/providers/sms.py` into `apps/workers/providers/sms.*`
- [ ] Pulls from `outbound-actions` queue
- [ ] Handler for `action='sms'`:
  - [ ] Load `business.hours`, `business.timezone`, `contact.contact_preferences`
  - [ ] Confirm SMS opt-in + quiet hours
  - [ ] Render template with Jinja-style vars: `{{business.name}}`, `{{contact.first_name}}`
  - [ ] Call Retell SMS API (fallback to Twilio per `sms.py` pattern)
  - [ ] Write `conversation_messages` row with direction='outbound', provider_message_id, status='sent'
  - [ ] Write `events` row: `event_name='outbound.sms.sent'`
- [ ] Retry on 5xx / timeout; drop on 4xx

### 5. Reply handling (Twilio-style inbound)

- [ ] Port `apps/api-gateway/app/webhooks_twilio.py` → `apps/webhooks/app/twilio.py` (or reuse Retell SMS webhook if configured via Retell)
- [ ] On inbound SMS, emit `message.received` → `inbound-events` queue
- [ ] `inbound_normalizer_worker` handler for `message.received`:
  - [ ] Match existing conversation by (contact_id, channel_id, status='open')
  - [ ] Append `conversation_messages` row
  - [ ] Enqueue `conversation-intelligence` job for intent re-classification
- [ ] Handle STOP/START/HELP (set `contact_preferences.sms_opt_out`)

### 6. `conversation_intelligence_worker` (minimal)

- [ ] Pulls from `conversation-intelligence` queue
- [ ] On new inbound message:
  - [ ] Call LLM with full conversation transcript + `knowledge_items` (grounding) + SKILL.md prompt
  - [ ] Return structured JSON: `{intent, confidence, fields_collected, next_action, handoff_reason?}`
  - [ ] Update `leads` row with `intent` + `qualification_fields`
  - [ ] If `next_action='reply'`: enqueue `outbound-actions` with generated reply text
  - [ ] If `next_action='handoff'`: enqueue `handoffs` job
  - [ ] If `next_action='book'`: defer to Phase 4
- [ ] Guardrail: never send outbound reply without either (a) matched `knowledge_items` grounding, or (b) pre-approved template

### 7. `handoff_worker` creating tasks

- [ ] Pulls from `handoffs` queue
- [ ] Insert `tasks` row with:
  - `kind='handoff'`, `priority` based on intent urgency, `due_at=now() + hours` per SKILL.md policy
  - `context_jsonb` containing conversation summary + suggested reply
- [ ] Notify operator via email or dashboard websocket (MVP: email only)
- [ ] Write `events` row

### 8. Dashboard — Inbox page

- [ ] Next.js page `/inbox` lists `conversations` with latest message + status + assigned task
- [ ] Conversation detail page: message thread, lead card, task card with "Complete" button
- [ ] Completing a task writes `tasks.status='done'`, closes conversation if flagged

### 9. Dashboard — Phone settings page

- [ ] `/settings/channels` lists `channels` rows
- [ ] Add channel form: voice or sms, Retell/Twilio config
- [ ] Smoke: add a channel, verify it lands in DB, verify Retell webhook URL shown

### 10. Metrics — minimal rollup

- [ ] `apps/api/app/scheduler.py` (or standalone `re-rollup` service) runs nightly
- [ ] Computes per-business daily counts: `missed_calls`, `missed_calls_recovered`, `replies_received`, `tasks_created`, `tasks_completed`
- [ ] Inserts into `metric_snapshots` with `date`, `business_id`, `metrics jsonb`

### 11. Observability

- [ ] Sentry DSN set in all services
- [ ] `trace_id` propagated through headers (webhook → enqueue → worker → API calls)
- [ ] `/metrics` endpoint on `re-api` exposes counts of: `queue_jobs` by status, `events` by name, worker lag
- [ ] Log sampling rate documented

### 12. Smoke test

- [ ] `scripts/smoke_phase1.sh`:
  1. `curl` POST to `re-webhooks` with fake Retell `call.ended` payload (no-answer variant)
  2. Poll `conversations` where `channel_id=...` until row appears (timeout 10s)
  3. Poll `conversation_messages` where direction='outbound' (timeout 30s)
  4. POST fake inbound SMS reply
  5. Poll `tasks` where `conversation_id=...` until row appears (timeout 30s)
  6. Assert all 4 rows exist and share a `trace_id`

---

## Out of scope for Phase 1

- Human operator UX polish beyond a functional inbox
- Any booking / calendar integration (Phase 4)
- Any quote-intake or media-upload handling (Phase 3)
- Reactivation campaigns (Phase 5)
- Multi-language (English only for MVP)
- Ringless voicemail drops
- Voice-first outbound (text-only outreach for Phase 1)

---

## Risks to watch

1. **Retell webhook flakiness** — mitigate with idempotency key on `retell:<call_id>`
2. **SMS throttling** — add a per-contact + per-business rate limit before the Retell/Twilio HTTP call
3. **Transcript arrival lag** — `call.ended` may fire before `call_analyzed` completes. Keep them separate: enqueue on both, let the worker do best-effort intent based on whatever's present.
4. **RLS bypass in workers** — workers use service role; log every cross-business read explicitly and audit.
5. **Sticky conversations** — deciding which conversation a new inbound message "belongs to" is tricky when contact has multiple open threads. For MVP: always match to the most recent open conversation for that contact+channel+direction pair.

---

## Completion criteria

When the smoke test passes three times in a row over a live Supabase instance, Phase 1 is done. Move to Phase 2 (after-hours intake + FAQ-to-conversion).
