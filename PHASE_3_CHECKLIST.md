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

- [ ] `services` table already exists in schema; add API CRUD:
  - `GET /v1/services` — list active services for business
  - `POST /v1/services` — create (`{name, description, base_price_low, base_price_high, required_intake_fields, tags}`)
  - `PATCH /v1/services/:id` — update
- [ ] Each service defines `required_intake_fields` (e.g., `["name", "phone", "address", "scope", "urgency"]`)
- [ ] Seed default services via `seed_mvp_defaults.sql` extension or `seed_business.py` flag
- [ ] Wire `routes/services.py` into `main.py`

### 2. Enhanced conversation_intelligence — multi-turn intake extraction

- [ ] Extend the LLM system prompt with a `## Field Collection` section:
  ```
  When intent is quote_request or booking_request:
  1. Match the customer's request to a service from the business's active services list.
  2. Check which required_intake_fields for that service are still missing.
  3. Ask for exactly ONE missing field at a time — do not batch multiple questions.
  4. When all required fields are collected, set recommended_next_action = "draft_quote".
  5. If the customer provides unsolicited fields, capture them anyway.
  ```
- [ ] Add `services` to the LLM context: load `services` rows (active only) for the business and include them in the prompt
- [ ] New `recommended_next_action` values to handle:
  - `ask_question` — one field missing → enqueue `outbound-actions` with the generated question
  - `draft_quote` — all fields collected → enqueue `quote-drafting` job
- [ ] `fields_collected` from the LLM response:
  - Upsert into `intake_fields` (one row per field, linked to `lead_id`)
  - Update `leads.service_id` if a service match was identified
  - Advance `leads.stage` to `qualified` if all required fields are present

### 3. Intake fields persistence — `lib/leads.py` extension

- [ ] `upsert_intake_fields(lead_id, fields_collected, source_message_id)`:
  - For each key/value in `fields_collected`:
    - Insert or update `intake_fields` row (unique on `lead_id, field_name`)
    - Set `confidence` from the LLM's per-field confidence if available
    - Set `source_message_id` to the message that contained the field
- [ ] `check_required_fields_complete(lead_id, service_id)`:
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

- [ ] New worker: `quote_drafting_worker.py`, consumes `quote-drafting` queue
- [ ] Register in `main.py` worker registry
- [ ] Job payload:
  ```json
  {
    "lead_id": "uuid",
    "conversation_id": "uuid",
    "business_id": "uuid",
    "service_id": "uuid|null",
    "trace_id": "..."
  }
  ```
- [ ] On claim:
  1. Load lead + intake_fields + service + business
  2. Match pricing: check `services.base_price_low / base_price_high` and `services.price_policy`
  3. Render `quotes.draft_text` using a quote template:
     - If `message_templates` has a `quote_template` for this service → render with intake fields as context
     - Otherwise, generate a structured draft via LLM with strict instructions: "Use only approved pricing from the service record. If no price range exists, note that pricing requires human review."
  4. Insert `quotes` row:
     - `status = 'awaiting_review'` (default)
     - `quote_type = 'estimate'`
     - `amount_low / amount_high` from service pricing (if available)
     - `draft_text` = rendered template
  5. Create `tasks` row: `type = 'quote_review'`, `source_table = 'quotes'`, `source_id = quote.id`
  6. Advance `leads.stage` to `awaiting_quote`
  7. Emit `quote.drafted` event
- [ ] Hard blocks (→ create `human-handoff` task instead of drafting):
  - No matching service found
  - No approved pricing rule
  - Missing required fields (should not happen if intelligence did its job, but defensive)
  - High-value scope: `estimated_value_high > business.settings.auto_quote_max` → human review
- [ ] Add `WorkerSettings.auto_quote_max` (default: no limit for MVP)

### 6. Quote review + send — API endpoints

- [ ] `apps/api/app/routes/quotes.py`:
  - `GET /v1/quotes` — list quotes for business (filterable by status)
  - `GET /v1/quotes/:id` — detail (includes linked lead, intake_fields, conversation)
  - `PATCH /v1/quotes/:id` — operator edits: `draft_text`, `amount_low`, `amount_high`, `terms`
  - `POST /v1/quotes/:id/approve` — sets `approved_by`, flips status to `sent`, enqueues `outbound-actions` with `action = 'send_quote'`
  - `POST /v1/quotes/:id/decline` — sets `status = 'void'`, optional `decline_reason`
- [ ] Wire into `main.py`

### 7. Quote send via outbound_action

- [ ] Extend `outbound_action` to handle `action = 'send_quote'`:
  - Load `quotes` row, `contacts` row, `business`
  - Render the quote body for SMS (truncated to 320 chars with link to full) or email (full text)
  - Send via SMS (preferred) or email (if `contacts.email` exists and `contacts.metadata.prefer_email`)
  - Update `quotes.sent_at`, `quotes.status = 'sent'`
  - Insert outbound `messages` row
  - Emit `quote.sent` event
- [ ] Advance `leads.stage` to `quoted` after send

### 8. Quote recovery follow-up

- [ ] After `quote.sent` event, `quote_drafting_worker` enqueues a `follow-up-scheduler` job:
  ```json
  {
    "followup_type": "quote_recovery",
    "lead_id": "<uuid>",
    "quote_id": "<uuid>",
    "conversation_id": "<uuid>",
    "business_id": "<uuid>",
    "attempt": 1,
    "max_attempts": 3,
    "delays_days": [2, 4, 7]
  }
  ```
- [ ] `followup_scheduler` logic for `quote_recovery`:
  - Check stop conditions: customer replied, lead stage is `won`/`lost`/`booked`, human override, max attempts
  - If not stopped:
    - Select template by attempt: `quote_followup_1`, `quote_followup_2`, `quote_followup_final`
    - Enqueue `outbound-actions`
    - If `attempt < max_attempts`: re-enqueue self with `available_at = now() + delays_days[attempt]`
- [ ] When customer replies to a quote follow-up:
  - `inbound_normalizer` routes to `conversation-intelligence`
  - Intelligence re-classifies; if customer wants to proceed → advance lead to `booked` or re-engage
  - If customer declines → update `leads.stage = 'lost'`, `leads.lost_reason`

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
