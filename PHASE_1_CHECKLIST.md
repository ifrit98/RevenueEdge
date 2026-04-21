# Phase 1 Checklist — Missed-Call Recovery

**Goal**: A configured business with an inbound Retell number gets a text within 60 seconds of a missed call, and the conversation reaches a human-confirmable state.

This is the first user-visible vertical slice. Everything before it is scaffolding; everything after builds on it.

**Implementation status (updated after Phase 1 backend pass)**

Backend pipeline is functionally complete end-to-end:
- Shared worker library (`apps/workers/src/lib/*`) for phone/channel/contact/
  conversation/template/hours/LLM helpers.
- `inbound_normalizer` resolves the business from the inbound DID, upserts the
  contact, opens the conversation, persists the message, and fans out to
  `outbound-actions` (on `call.missed`) + `conversation-intelligence`.
- `conversation_intelligence` classifies via OpenAI JSON mode (or a safe
  heuristic when no API key is set), applies a ≥0.72 confidence floor for
  autopilot, and routes to outbound / handoff.
- `outbound_action` renders seeded SMS templates (Liquid-ish subset) and
  sends via Retell SMS with Twilio fallback, opt-out aware.
- `handoff` creates a `tasks` row, flips the conversation to
  `awaiting_human`, and optionally emails `businesses.escalation.email`.
- `re-api` exposes `/v1/businesses`, `/v1/conversations`, `/v1/tasks`,
  `/v1/metrics`, `/v1/metrics/rollup` and runs an in-process rollup
  scheduler (`metrics_rollup` service).
- `scripts/seed_business.py` + `scripts/smoke_phase1.sh` drive the full
  happy path via the real webhook endpoint.

Dashboard UI (items 8–9) is still deferred until a live Supabase project is
wired up.

---

## Definition of done

- [x] Inbound Retell voice call where agent doesn't answer → SMS sent to the caller within 60s (pipeline implemented; live verification pending live Supabase)
- [x] SMS reply from caller → conversation updated, intent classified, task created if handoff needed
- [ ] Dashboard inbox shows the conversation with transcript, intent, lead record, task *(backend APIs ready; UI deferred)*
- [x] `metric_snapshots` row shows `missed_calls`/`recovered_leads` counts
- [x] End-to-end trace (trace_id) visible across `queue_jobs.payload`, `events`, `conversations.messages`, `tasks`
- [~] Rate limits: no SMS to STOP'd numbers *(opt-out respected; per-contact/per-business rate limit deferred to Phase 2)*
- [x] Smoke test: `scripts/smoke_phase1.sh` seeds a business, simulates a missed call, asserts downstream effects *(requires live services to execute)*

---

## Order of operations

### 1. Supabase ready (depends on: Phase 0 migrations applied)

- [ ] `schema.sql` applied to a fresh Supabase project *(awaiting new project provisioning)*
- [ ] `seed_mvp_defaults.sql` applied (FAQ templates, vertical presets, workflow definitions)
- [x] `scripts/seed_business.py` idempotently inserts a test `businesses` row + phone/SMS `channels` + invokes `seed_revenue_edge_mvp_defaults`
- [ ] Confirm RLS policies block cross-business reads: run a test query as anon + wrong business_id
- [x] `claim_queue_jobs` RPC works: `scripts/smoke_phase0.sh` enqueues, claims, completes

### 2. `re-webhooks` service receiving Retell events

- [x] Port `apps/api-gateway/app/webhooks_retell.py` signature verification into `apps/webhooks/app/retell.py`
- [x] Route handler: on `call.ended` or `call_analyzed`, canonicalize to `call.missed` / `call.ended` / `message.received`; business lookup deferred to `inbound_normalizer` to keep the webhook under the 500ms budget
- [x] Build `inbound-events` payload per pack contract:
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
- [x] Enqueue via `select enqueue_job('inbound-events', …)` (through the internal `/internal/queue/enqueue` endpoint so the webhook never talks to Supabase directly)
- [x] Return `2xx` fast (< 500ms), even if job enqueue fails (log + keep event replay window alive)
- [ ] Integration test: POST a fake Retell webhook payload, assert row lands in `queue_jobs` *(automated via `smoke_phase1.sh` against live stack)*

### 3. `inbound_normalizer_worker` reading `inbound-events`

- [x] Worker scaffold polls `claim_queue_jobs('inbound-events', worker_id, batch, timeout)` via `BaseWorker`
- [x] Handler for `call.missed` / `call.ended` / `message.received`:
  - [x] Resolve `business_id` by `channels.external_id = to_number`
  - [x] Upsert `contacts` row by `phone_e164`
  - [x] Create / reuse open `conversations` row scoped by (business, contact, channel_type)
  - [x] Insert `messages` row (voicemail transcript, inbound SMS body, or metadata note)
  - [ ] Seed `leads` row *(deferred — current flow creates a lead row only when intelligence classifies intent. Dedicated lead creation for every inbound ticket moves to Phase 2 so we don't spam the lead table with no-show calls.)*
  - [x] Enqueue `outbound-actions` with `template_name='missed_call_recovery'` on `call.missed`
  - [x] Enqueue `conversation-intelligence` for classification
  - [x] Write `events` row via `enqueue_event` (idempotency via unique key)
  - [x] `complete_queue_job` / `fail_queue_job` with exponential backoff in `BaseWorker`
- [x] Idempotency: `events.idempotency_key = call_id:<canonical>` and messages `idempotency_key = msg:inbound:<event>:<external_id>`

### 4. `outbound_action_worker` sending SMS

- [x] Providers duplicated into `apps/workers/src/providers/{sms,email}.py` (Retell primary, Twilio fallback, SendGrid for email)
- [x] Pulls from `outbound-actions` queue
- [x] Handler for `action='send_sms'`:
  - [x] Load contact, conversation, business
  - [x] Respect `contacts.metadata.sms_opt_out`
  - [x] Render template via `lib/templates.render_template` with Liquid-ish placeholder subset
  - [x] Call Retell SMS with Twilio fallback
  - [x] Insert outbound `messages` row (direction='outbound', sender_type='ai', `raw_payload.provider` recorded)
  - [x] `outbound.sms.sent` event
- [x] Retryable vs permanent errors distinguished in `BaseWorker`
- [ ] Quiet-hours enforcement *(business-hours helper exists but is not yet enforced — default MVP stance is "always allow" on user confirmation; quiet-hours policy lands when a business toggles it on)*

### 5. Reply handling (inbound SMS)

- [x] Retell webhook canonicalizes `call_message`/`chat_message_created` to `message.received` and routes back through the same `inbound-events` worker
- [x] `inbound_normalizer` handler for `message.received` reuses open conversation (or opens a new one) and appends a message row
- [x] Enqueue `conversation-intelligence` for intent re-classification
- [ ] STOP/START/HELP handling *(deferred — Retell handles SMS compliance when the DID is provisioned via Retell; when we move to a direct Twilio fallback we'll add this to `inbound_normalizer` explicitly)*

### 6. `conversation_intelligence_worker` (minimal)

- [x] Pulls from `conversation-intelligence` queue
- [x] On new inbound event:
  - [x] Load full conversation context via `lib/conversations.load_conversation_context`
  - [x] Call OpenAI chat completions (JSON mode) with the SKILL-aligned system prompt; fall back to heuristic when `OPENAI_API_KEY` is empty
  - [x] Return `{intent, urgency, confidence, recommended_next_action, reply_text, fields_collected, handoff_reason, summary}`
  - [x] Update `conversations` with `current_intent`, `urgency`, `ai_confidence`, `summary`, `metadata.last_decision`
  - [x] Route to `outbound-actions` / `human-handoff` based on `recommended_next_action`
  - [ ] Persist `leads.fit_score` / `leads.stage` *(deferred; the decision is currently captured on the conversation row + events and will bridge to `leads` in Phase 2 along with the richer lead lifecycle)*
- [x] Guardrail: confidence < 0.72 → force handoff

### 7. `handoff_worker` creating tasks

- [x] Pulls from `human-handoff` queue
- [x] Insert `tasks` row with:
  - `type='human_handoff'`, priority derived from urgency, `source_table='conversations'`, `source_id=<conversation_id>`, `metadata` carrying intent + trace
- [x] Flip conversation to `status='awaiting_human'`
- [x] Optional operator email when `businesses.escalation.email` is set
- [x] Emit `conversation.handoff_created` event

### 8. Dashboard — Inbox page

- [x] Backend: `/v1/conversations` list + detail routes
- [x] Backend: `/v1/tasks` list + PATCH for status/priority
- [ ] Next.js page `/inbox` *(deferred — dashboard UI slice will ship as a follow-up once a live Supabase project is provisioned and JWT flow is verified)*

### 9. Dashboard — Phone settings page

- [x] Backend: `scripts/seed_business.py` covers bootstrap; CRUD for channels deferred until the settings UI is built
- [ ] Next.js page `/settings/channels` *(deferred alongside item 8)*

### 10. Metrics — minimal rollup

- [x] `apps/api/app/services/scheduler.py` runs an in-process asyncio loop (`METRICS_ROLLUP_INTERVAL_SECONDS`, default 10min) calling `run_daily_rollup`
- [x] `run_daily_rollup` aggregates events/leads/quotes/bookings/messages into `metric_snapshots` (upserted on `(business_id, metric_date)`)
- [x] `/v1/metrics` (authenticated read) + `/v1/metrics/rollup` (internal trigger)

### 11. Observability

- [x] Sentry init in both `re-api` and `re-workers` (no-op when DSN empty)
- [x] `trace_id` propagated from webhook → queue payload → worker logs → events
- [ ] `/metrics` Prom-style counters endpoint *(deferred — `/v1/metrics` + `/internal/queue/dead-letter/count` cover the MVP dashboard needs)*
- [x] Structured JSON logging in all services

### 12. Smoke test

- [x] `scripts/seed_business.py` idempotently creates the test business + phone/SMS channels
- [x] `scripts/smoke_phase1.sh`:
  1. Seeds the business
  2. POSTs a synthetic Retell `call_ended` (missed variant) to `re-webhooks`
  3. Polls `events` for `outbound.sms.sent`, `conversation.classified`, and either `conversation.handoff_created` or an outbound `messages` row
  4. Fails loudly if any outcome is missing inside the 60s window

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
