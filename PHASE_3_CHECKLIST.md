# Phase 3 Checklist — Qualification + Quote Intake

**Goal**: `quote_request` conversations collect required intake fields via multi-turn SMS, create a structured draft quote for human review, and track follow-up until closure.

**Depends on**: Phase 2 complete (leads lifecycle, knowledge retrieval, quiet-hours, rate limits).

**Exit criteria**: Operator reviews a draft quote < 5 minutes after an inbound inquiry, approves with one click, and the system tracks follow-up until closure or loss.

---

## Definition of done

- [ ] Inbound "I need a quote for X" → multi-turn intake collection → all required fields gathered
- [ ] `leads` row transitions: `new → contacted → qualified → awaiting_quote`
- [ ] `intake_fields` rows: each collected datum linked to lead + source message
- [ ] `quotes` row created with `status = 'awaiting_review'` and `draft_text` rendered from template
- [ ] Photo request SMS sent when `service.photo_helpful = true` and no photos received
- [ ] Operator sees draft quote in dashboard, can edit + approve + one-click send
- [ ] `quote.sent` → outbound SMS/email to customer with quote summary
- [ ] Quote recovery follow-up: 3-attempt cadence (day 2, day 4, final) auto-stops on reply/win/loss
- [ ] `metric_snapshots`: `quotes_sent`, estimate turnaround seconds
- [ ] Smoke test: `scripts/smoke_phase3.sh` simulates a multi-turn quote intake, asserts quote created + follow-up scheduled

---

## Order of operations

### 1. Services configuration

- [x] `services` table already exists in schema; add API CRUD:
  - `GET /v1/services` — list active services for business
  - `POST /v1/services` — create (`{name, description, base_price_low, base_price_high, required_intake_fields, tags}`)
  - `PATCH /v1/services/:id` — update
- [x] Each service defines `required_intake_fields` (e.g., `["name", "phone", "address", "scope", "urgency"]`)
- [ ] Seed default services via `seed_mvp_defaults.sql` extension or `seed_business.py` flag
- [x] Wire `routes/services.py` into `main.py`

### 2. Enhanced conversation_intelligence — multi-turn intake extraction

- [x] Extend the LLM system prompt with a `## Field Collection` section
- [x] Add `services` to the LLM context: load `services` rows (active only) for the business and include them in the prompt
- [x] New `recommended_next_action` values to handle:
  - `ask_question` — one field missing → enqueue `outbound-actions` with the generated question
  - `draft_quote` — all fields collected → enqueue `quote-drafting` job
- [x] `fields_collected` from the LLM response:
  - Upsert into `intake_fields` (one row per field, linked to `lead_id`)
  - Update `leads.service_id` if a service match was identified
  - Advance `leads.stage` to `qualified` if all required fields are present

### 3. Intake fields persistence — `lib/leads.py` extension

- [x] `upsert_intake_fields(lead_id, fields_collected, source_message_id)`:
  - For each key/value in `fields_collected`:
    - Insert or update `intake_fields` row (unique on `lead_id, field_name`)
    - Set `confidence` from the LLM's per-field confidence if available
    - Set `source_message_id` to the message that contained the field
- [x] `check_required_fields_complete(lead_id, service_id)`:
  - Load `services.required_intake_fields`
  - Load current `intake_fields` for the lead
  - Return `(complete: bool, missing: list[str])`

### 4. Photo request flow

- [ ] In `conversation_intelligence`, when:
  - `intent = quote_request` AND `service.tags` includes `photo_helpful` (or `service.metadata.photo_helpful = true`)
  - AND no `intake_fields` row with `field_name = 'photos'` exists for this lead
  - → include `photos` in `missing_fields` list
  - → when the only remaining missing field is `photos`, send the `photo_request` template
- [ ] Inbound MMS handling:
  - Retell surfaces MMS attachments in the webhook `call.metadata` or `message.attachments`
  - `inbound_normalizer` persists attachment URLs in `messages.attachments` (JSONB array)
  - `conversation_intelligence` detects attachments → sets `fields_collected.photos = [urls]`
  - Fallback: if MMS not supported, the `photo_request` template includes a link to a simple upload page (Phase 3b — can defer to a hosted upload endpoint)

### 5. Quote drafting worker

- [x] New worker: `quote_drafting.py`, consumes `quote-drafting` queue
- [x] Register in `main.py` worker registry + settings
- [x] Job payload matches spec
- [x] On claim:
  1. Load lead + intake_fields + service + business
  2. Match pricing: check `services.base_price_low / base_price_high`
  3. Render `quotes.draft_text` using structured format
  4. Insert `quotes` row with `status = 'awaiting_review'`
  5. Create `tasks` row with `type = 'quote_review'`
  6. Advance `leads.stage` to `proposal`
  7. Emit `quote.drafted` event
- [x] Hard blocks: no service + no intake fields → escalate to handoff
- [ ] Add `WorkerSettings.auto_quote_max` (default: no limit for MVP)

### 6. Quote review + send — API endpoints

- [x] `apps/api/app/routes/quotes.py`:
  - `GET /v1/quotes` — list quotes for business (filterable by status)
  - `GET /v1/quotes/:id` — detail (includes linked lead, contact)
  - `PATCH /v1/quotes/:id` — operator edits: `draft_text`, `amount_low`, `amount_high`, `terms`
  - `POST /v1/quotes/:id/approve` — sets `approved_by`, flips status, enqueues `outbound-actions` with `action = 'send_quote'`
  - `POST /v1/quotes/:id/decline` — sets `status = 'void'`, optional `decline_reason`
- [x] Wire into `main.py`

### 7. Quote send via outbound_action

- [x] Extend `outbound_action` to handle `action = 'send_quote'`:
  - Load `quotes` row, `contacts` row, `business`
  - Render the quote body for SMS (truncated to 320 chars)
  - Send via SMS
  - Update `quotes.sent_at`, `quotes.status = 'sent'`
  - Insert outbound `messages` row
  - Emit `quote.sent` event
- [x] Advance `leads.stage` to `proposal` after send
- [x] Enqueue quote_recovery follow-up after send

### 8. Quote recovery follow-up

- [x] After `quote.sent`, `outbound_action._handle_send_quote` enqueues a `follow-up-scheduler` job with `delays_days: [2, 4, 7]`
- [x] `followup_scheduler` logic for `quote_recovery`:
  - Check stop conditions: customer replied, lead stage is `won`/`lost`/`booked`, max attempts
  - If not stopped:
    - Select template by attempt: `quote_followup_1`, `quote_followup_2`, `quote_followup_final`
    - Enqueue `outbound-actions`
    - If `attempt < max_attempts`: re-enqueue self with exponential delay
- [x] When customer replies to a quote follow-up:
  - `inbound_normalizer` routes to `conversation-intelligence` (existing flow)
  - Intelligence re-classifies; natural lead-stage advancement applies

### 9. Metrics additions

- [ ] `metrics_rollup.py` additions:
  - `quotes_sent` — count of `quote.sent` events per day (already a column in `metric_snapshots`)
  - `estimate_turnaround_seconds` — average time from `lead.created` to `quote.sent` (store in `metric_snapshots.payload`)
  - `quote_recovery_wins` — leads that went from `quoted` to `won` after a follow-up (store in payload)

### 10. Smoke test

- [ ] `scripts/smoke_phase3.sh`:
  1. Seed a business with one service (e.g., "Drain Cleaning") with `required_intake_fields = ["name", "address", "scope"]` and `base_price_low = 150, base_price_high = 350`
  2. Simulate inbound SMS: "I need a quote for a drain cleaning"
  3. Assert: intelligence classifies as `quote_request`, asks for missing field (e.g., address)
  4. Simulate reply: "123 Main St, clogged kitchen sink"
  5. Assert: `intake_fields` rows created, fields complete check passes
  6. Assert: `quotes` row with `status = 'awaiting_review'`
  7. Assert: `tasks` row with `type = 'quote_review'`
  8. Approve the quote via API (`POST /v1/quotes/:id/approve`)
  9. Assert: `outbound.sms.sent` event with quote text
  10. Assert: `follow-up-scheduler` job enqueued for day 2

---

## Out of scope for Phase 3

- Calendar booking / availability checking (Phase 4)
- Reactivation campaigns (Phase 5)
- Complex pricing engines or multi-item quotes
- Deposit/payment collection
- Contractual / DocuSign signing flows
- Multi-format quote output (PDF, email with branded template)

---

## Risks to watch

1. **Multi-turn coherence** — LLM may forget previously collected fields if the conversation is long. Mitigate by injecting the current `intake_fields` summary into every classification call.
2. **Price hallucination** — the LLM must never invent prices. The prompt enforces this, and `quote_drafting_worker` double-checks against `services.price_policy`. Log any delta between LLM-suggested price and service range.
3. **MMS reliability** — Retell's MMS surface may not be fully stable. Keep photo collection optional and fall back to "please describe the issue in text" if MMS fails.
4. **Quote follow-up fatigue** — 3 attempts is the default; make it configurable per-business via `business_rules` or `businesses.settings.quote_followup_max_attempts`.
