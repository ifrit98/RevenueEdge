# Phase 2 Checklist — After-Hours Intake + FAQ-to-Conversion

**Goal**: Inbound SMS works outside hours; FAQs are grounded in approved knowledge; the `leads` table has a real lifecycle; the agent enforces quiet-hours and per-contact rate limits.

**Depends on**: Phase 1 complete + live Supabase + smoke test green.

**Exit criteria**: Business owner uploads 10 FAQ items, marks them approved, and a real inbound "what are your hours?" SMS gets a grounded answer plus a buying-intent follow-up question — all without waking a human outside hours.

---

## Definition of done

- [ ] After-hours inbound SMS → conversation opens, auto-reply using `after_hours_intake` template, and creates a follow-up task for next-business-open
- [ ] FAQ question → answer grounded in approved `knowledge_items` via pgvector cosine + lexical fallback
- [ ] Knowledge gap → `knowledge_review` task created, honest "I'll have the team confirm that" fallback sent
- [ ] Quiet-hours enforcement: no outbound SMS before 08:00 or after 20:00 local (per `workflow_defaults.quiet_hours`), emergency override honored
- [ ] Per-contact SMS rate limit: max 1 SMS per contact per 2 minutes
- [ ] `leads` rows created from classified conversations; stage transitions (`new → contacted → qualified`)
- [ ] `intake_fields` rows persisted from LLM `fields_collected`
- [ ] Dashboard: knowledge CRUD API + handoffs filtered by `tasks.type = 'knowledge_review'`
- [ ] Smoke test: `scripts/smoke_phase2.sh` seeds knowledge items, simulates after-hours SMS, asserts grounded reply + knowledge-gap task

---

## Order of operations

### 1. Quiet-hours enforcement in outbound_action

- [ ] Before sending, call `lib/hours.is_within_business_hours(business)` (already written)
- [ ] If outside hours **and** not tagged `emergency_override`:
  - Compute `next_open_at` from `businesses.hours.weekly` + timezone
  - Enqueue a delayed `outbound-actions` job with `available_at = next_open_at`
  - Return `{"deferred": true, "available_at": next_open_at}` as job result
- [ ] If the message template has `metadata.autopilot_safe = true` **and** it's the first contact on this conversation, exempt from quiet-hours (the initial textback is time-sensitive)
- [ ] Emergency override: `urgency = 'emergency'` or `business_rules.emergency_override_allowed = true` bypasses quiet-hours

### 2. Per-contact SMS rate limit

- [ ] Add `lib/rate_limit.py` to workers:
  - Uses `events` table: count `outbound.sms.sent` events for the same `contact_id` in the last N seconds
  - Default: 1 SMS per contact per 120 seconds
  - If limit hit, defer the outbound job (re-enqueue with `available_at = now() + remaining_cooldown`)
- [ ] `outbound_action` calls the rate-limit check before rendering + sending
- [ ] Configurable via `businesses.settings.sms_rate_limit_seconds` (default 120)

### 3. After-hours intake workflow

- [ ] In `conversation_intelligence`, after classification:
  - Check `lib/hours.is_within_business_hours(business)`
  - If outside hours **and** intent is not `emergency`:
    - Override `recommended_next_action` to `send_sms_reply` using `after_hours_intake` template
    - Enqueue a `follow-up-scheduler` job with `delay_until = next_business_open`
    - Set conversation `status = 'awaiting_customer'`
- [ ] The follow-up-scheduler (now real, not a stub) checks at `next_business_open`:
  - If no customer reply since the after-hours message → create a `followup` task for a human to review
  - If customer replied → conversation was already re-classified by `inbound_normalizer` → `conversation-intelligence`; no extra action

### 4. Follow-up scheduler — Phase 2 implementation

- [ ] Replace `followup_scheduler.py` stub with real logic
- [ ] Job payload contract:
  ```json
  {
    "conversation_id": "uuid",
    "business_id": "uuid",
    "followup_type": "after_hours_review | no_reply_check | quote_recovery | reactivation",
    "attempt": 1,
    "max_attempts": 4,
    "trace_id": "..."
  }
  ```
- [ ] On claim:
  - Load conversation + latest messages
  - **Stop conditions** (per `workflow_defaults.followup_limits`):
    - Customer replied since the followup was scheduled → complete as no-op
    - Lead stage is `won`, `lost`, `booked` → complete
    - Human override (conversation `status = 'awaiting_human'`) → complete
    - `attempt >= max_attempts` → complete
  - If not stopped: enqueue appropriate downstream job (`outbound-actions` for auto-followup, `human-handoff` for escalation)
- [ ] For `after_hours_review`: enqueue a `human-handoff` job with `task_type = 'followup'` and summary "Customer did not respond to after-hours message; review needed"

### 5. Leads lifecycle — bridge from conversations

- [ ] After `conversation_intelligence` classifies and confidence >= threshold:
  - If `intent` is in `{quote_request, booking_request, urgent_service, support}` and no existing `leads` row for this (business, contact, conversation):
    - Insert `leads` row: `stage = 'new'`, `urgency`, `service_requested` (from `fields_collected`), `source = channel_type`
    - Emit `lead.created` event
  - If fields were collected (`fields_collected` is non-empty):
    - Upsert `intake_fields` rows: one per key in `fields_collected`, linked to lead + optional source message
    - Emit `lead.qualified` if all `services.required_intake_fields` are present → advance stage to `qualified`
  - Advance `leads.stage`:
    - `new → contacted` when first outbound message is sent
    - `contacted → qualified` when required fields are complete
    - `qualified → awaiting_quote` when a `quote.requested` event fires (Phase 3)
- [ ] `conversation_intelligence` returns `lead_id` in its result so downstream workers can reference it
- [ ] Add `lib/leads.py` to worker library with `create_or_find_lead()` and `advance_lead_stage()` helpers

### 6. Knowledge retrieval — pgvector + lexical hybrid

- [ ] Add `lib/knowledge.py` to worker library:
  - `retrieve_relevant_knowledge(business_id, query_text, limit=5)`:
    1. Generate embedding for `query_text` via OpenAI `text-embedding-3-small`
    2. Vector search: `order by knowledge_items.embedding <=> query_embedding limit 3` (cosine distance via HNSW index)
    3. Lexical fallback: `ts_rank(search_tsv, plainto_tsquery(query_text))` for items not caught by vector (fuzzy match for abbreviations, brand names, etc.)
    4. Merge + deduplicate, filter to `active = true AND approved = true`
    5. Return list of `{id, title, content, type, similarity_score}`
  - `embed_text(text)` → calls OpenAI embedding endpoint, returns `list[float]`
- [ ] In `conversation_intelligence`, before LLM classification call:
  - Extract the latest customer message body
  - Call `retrieve_relevant_knowledge` with that body
  - Inject matching knowledge items into the LLM system prompt as a `## Business Knowledge` section
  - The LLM prompt instructs: "Answer only from the provided knowledge items. If no item covers the question, set `knowledge_missing = true`."
- [ ] If `knowledge_missing = true` in the LLM response:
  - Set `recommended_next_action = 'handoff'`
  - `handoff_reason = 'Knowledge gap: <customer question summary>'`
  - Use the fallback template: "I want to make sure I give you the right answer. I'll have the team confirm that and get back to you."
  - Downstream `handoff_worker` creates `tasks.type = 'knowledge_review'`

### 7. Knowledge ingestion worker

- [ ] New worker: `knowledge_ingestion_worker.py`, consumes `knowledge-ingestion` queue
- [ ] Register in `main.py` worker registry
- [ ] Job payload: `{"knowledge_item_id": "uuid", "business_id": "uuid", "action": "embed | re_embed | classify"}`
- [ ] On `embed`:
  - Load the `knowledge_items` row
  - Generate embedding via `lib/knowledge.embed_text(title + " " + content)`
  - Update `knowledge_items.embedding = <vector>`
  - If `review_required = true`, create a `knowledge_review` task
- [ ] On `re_embed`:
  - Same as embed but skips creating a new review task
- [ ] Trigger: API endpoint `POST /v1/knowledge` (create item) and `PATCH /v1/knowledge/:id` (update item) both enqueue a `knowledge-ingestion` job

### 8. Knowledge CRUD API

- [ ] `apps/api/app/routes/knowledge.py`:
  - `GET /v1/knowledge` — list knowledge items for business (paginated, filterable by type/active/approved)
  - `POST /v1/knowledge` — create item (body: `{title, content, type, tags}`) → auto-enqueue embedding job
  - `GET /v1/knowledge/:id` — detail
  - `PATCH /v1/knowledge/:id` — update (title, content, active, approved, tags) → re-embed if content changed
  - `DELETE /v1/knowledge/:id` — soft-delete (`active = false`)
- [ ] Knowledge sources CRUD (optional for Phase 2; sources could be URL, file upload, manual):
  - `POST /v1/knowledge-sources` — create source (e.g., URL to scrape, uploaded doc)
  - `GET /v1/knowledge-sources` — list
- [ ] Wire into `main.py`

### 9. API: channels CRUD

- [ ] `apps/api/app/routes/channels.py`:
  - `GET /v1/channels` — list channels for business
  - `POST /v1/channels` — create channel (`{channel_type, provider, external_id, display_name, config}`)
  - `PATCH /v1/channels/:id` — update (status, display_name, config)
  - `DELETE /v1/channels/:id` — soft-delete (`status = 'archived'`)
- [ ] Wire into `main.py`

### 10. API: leads CRUD

- [ ] `apps/api/app/routes/leads.py`:
  - `GET /v1/leads` — list leads for business (filterable by stage, urgency, source)
  - `GET /v1/leads/:id` — detail (includes intake_fields)
  - `PATCH /v1/leads/:id` — update stage, owner_user_id, notes
- [ ] Wire into `main.py`

### 11. STOP/START/HELP compliance

- [ ] In `inbound_normalizer`, before classification:
  - If inbound message body (trimmed, uppercased) is exactly `STOP`:
    - Set `contacts.metadata.sms_opt_out = true`
    - Do not enqueue any outbound or intelligence jobs
    - Emit `contact.opted_out` event
  - If body is `START`:
    - Set `contacts.metadata.sms_opt_out = false`
    - Emit `contact.opted_in` event
  - If body is `HELP`:
    - Enqueue outbound with a compliance-safe help message: "Reply STOP to unsubscribe. For support, call <business phone>."
    - Emit `contact.help_requested` event
- [ ] This only fires for `message.received` events where body exactly matches the keyword (not substring matches)

### 12. Observability additions

- [ ] Add `knowledge_gaps` to `metric_snapshots.payload`: count of `knowledge_review` tasks created per day
- [ ] Add `after_hours_leads` to `metric_snapshots.payload`
- [ ] Update `metrics_rollup.py` to aggregate these

### 13. Smoke test

- [ ] `scripts/smoke_phase2.sh`:
  1. Seed a business with `hours.weekly` set to weekday-only 09:00–17:00
  2. Seed 3 `knowledge_items` with content about services + hours
  3. Simulate inbound SMS at 22:00 local: "What time do you open tomorrow?"
  4. Assert: after-hours template sent (not a cold FAQ answer)
  5. Assert: conversation has `current_intent` set
  6. Simulate inbound SMS at 10:00 local: "How much does a drain cleaning cost?"
  7. Assert: grounded FAQ reply references the seeded knowledge item
  8. Simulate inbound SMS at 10:00 local: "Do you do roof repair?" (no matching knowledge)
  9. Assert: honest fallback message + `knowledge_review` task created
  10. Assert: `leads` row exists for at least one of the conversations

---

## Out of scope for Phase 2

- Quote drafting / quote lifecycle (Phase 3)
- Booking / calendar integration (Phase 4)
- Reactivation campaigns (Phase 5)
- Dashboard UI beyond API endpoints (follow-on sprint)
- Knowledge source scraping / document parsing (manual knowledge entry only)
- MMS / media handling

---

## Risks to watch

1. **Embedding quality** — `text-embedding-3-small` may not capture vertical-specific jargon well. Monitor retrieval precision in the first 50 conversations; switch to `text-embedding-3-large` if recall drops below 80%.
2. **Knowledge approval bottleneck** — if operators never approve items, the agent has no grounding material. Seed good defaults via `seed_mvp_defaults.sql` and consider auto-approving items from trusted sources.
3. **After-hours timezone edge cases** — businesses near timezone boundaries may see unexpected behavior. Log every quiet-hours decision with the resolved timezone + local time.
4. **Rate limit false positives** — a fast back-and-forth conversation could hit the 2-minute limit. Consider exempting replies-to-customer-message (only rate-limit proactive outreach).
