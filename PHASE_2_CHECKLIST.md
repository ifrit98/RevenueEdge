# Phase 2 Checklist — After-Hours Intake + FAQ-to-Conversion

**Goal**: Inbound SMS works outside hours; FAQs are grounded in approved knowledge; the `leads` table has a real lifecycle; the agent enforces quiet-hours and per-contact rate limits.

**Depends on**: Phase 1 complete + live Supabase + smoke test green.

**Exit criteria**: Business owner uploads 10 FAQ items, marks them approved, and a real inbound "what are your hours?" SMS gets a grounded answer plus a buying-intent follow-up question — all without waking a human outside hours.

---

## Definition of done

- [x] After-hours inbound SMS → conversation opens, auto-reply with after-hours message, follow-up task at next business open
- [x] FAQ question → answer grounded in `knowledge_items` via pgvector cosine + lexical fallback (retrieval layer done; LLM prompt injection TBD)
- [x] Knowledge gap → `knowledge_gap` task created on classification when no KB match + faq/objection/product_question intent
- [x] Quiet-hours enforcement: no outbound SMS during 21:00–08:00 local (configurable), deferred to next_business_open
- [x] Per-contact SMS rate limit: default 120s cooldown, daily cap per business
- [x] `leads` rows created from classified conversations; stage transitions (`new → contacted → qualified`)
- [ ] `intake_fields` rows persisted from LLM `fields_collected` (helper ready; LLM `fields_collected` extraction TBD)
- [x] Dashboard: knowledge CRUD API, channels CRUD API, leads CRUD API all wired
- [ ] Smoke test: `scripts/smoke_phase2.sh` (TBD — requires live Supabase)

---

## Order of operations

### 1. Quiet-hours enforcement in outbound_action

- [x] Before sending, call `lib/hours.is_quiet_hours(business)` — checks 9pm–8am local
- [x] If quiet hours: compute `next_business_open()` → enqueue deferred `outbound-actions` with `available_at`
- [x] Daily SMS cap check via `lib/rate_limit.check_daily_cap()`
- [ ] If the message template has `metadata.autopilot_safe = true` **and** it's the first contact on this conversation, exempt from quiet-hours (the initial textback is time-sensitive)
- [ ] Emergency override: `urgency = 'emergency'` or `business_rules.emergency_override_allowed = true` bypasses quiet-hours

### 2. Per-contact SMS rate limit

- [x] Add `lib/rate_limit.py` to workers:
  - Uses `events` table: count `outbound.sms.sent` events for the same `contact_id` in the last N seconds
  - Default: 1 SMS per contact per 120 seconds
  - If limit hit, defer the outbound job (re-enqueue with `available_at = now() + remaining_cooldown`)
- [x] `outbound_action` calls the rate-limit check before rendering + sending
- [x] Configurable via `businesses.settings.sms_rate_limit_seconds` (default 120)

### 3. After-hours intake workflow

- [x] In `conversation_intelligence`, after classification:
  - Check `lib/hours.is_within_business_hours(business)`
  - If outside hours **and** intent is not `emergency`:
    - Override `recommended_next_action` to `send_sms_reply` with after-hours message
    - Enqueue a `follow-up-scheduler` job with `available_at = next_business_open`
    - Set conversation `status = 'awaiting_customer'`
- [x] The follow-up-scheduler checks at `next_business_open`:
  - If no customer reply → create a `followup` task for a human to review
  - If customer replied → conversation was already re-classified; complete as no-op

### 4. Follow-up scheduler — Phase 2 implementation

- [x] Replace `followup_scheduler.py` stub with real logic
- [x] Job payload contract matches spec (conversation_id, business_id, followup_type, attempt, max_attempts, trace_id)
- [x] On claim: load conversation + latest messages
- [x] Stop conditions: customer replied, lead won/lost/booked, conversation closed/resolved, max attempts exceeded
- [x] `after_hours_review`: creates a followup task + escalation event
- [x] `no_reply_check`: re-enqueues self with exponential backoff (0h, 2h, 6h, 24h), escalates on final attempt

### 5. Leads lifecycle — bridge from conversations

- [x] `lib/leads.py` added with `find_or_create_lead()`, `advance_lead_stage()`, `upsert_intake_fields()`
- [x] `conversation_intelligence` creates/finds lead after classification (for non-spam/unknown intents)
- [x] Stage advances: `new → contacted` on send_sms_reply/ask_followup, `new/contacted → qualified` on collect_quote_details/schedule_callback
- [ ] Emit `lead.created` / `lead.qualified` events (deferred — events are written by the existing `enqueue_event` calls)
- [ ] Advance `qualified → awaiting_quote` when a `quote.requested` event fires (Phase 3)

### 6. Knowledge retrieval — pgvector + lexical hybrid

- [x] `lib/knowledge.py` with `embed_text()`, `retrieve_knowledge()`, semantic + lexical + RRF merge
- [x] `match_knowledge` Supabase RPC added in `supabase/migrations/0002_match_knowledge_rpc.sql`
- [x] `conversation_intelligence` extracts latest inbound message, retrieves knowledge articles, passes them as `knowledge_articles` in LLM context
- [x] Knowledge-missing fallback: if no KB articles match and intent is faq/objection/product_question, creates a `knowledge_gap` task
- [ ] Inject knowledge into LLM system prompt as `## Business Knowledge` section (requires LLM prompt update in `lib/llm.py`)
- [ ] LLM-level `knowledge_missing = true` detection (currently uses heuristic: empty KB results)

### 7. Knowledge ingestion worker

- [x] `knowledge_ingestion.py` worker: consumes `knowledge-ingestion` queue
- [x] Registered in `main.py` + `settings.py`
- [x] On `embed`: loads item, calls `embed_text()`, writes vector back to `knowledge_items.embedding`
- [x] On `re_embed`: same as embed, skips review task
- [x] On `review`: creates a `review_knowledge` task
- [x] Emits `knowledge.embed` / `knowledge.re_embed` / `knowledge.review` events

### 8. Knowledge CRUD API

- [x] `GET /v1/knowledge` — paginated, filterable by category/active
- [x] `POST /v1/knowledge` — create + auto-enqueue embedding job
- [x] `GET /v1/knowledge/:id` — detail
- [x] `PATCH /v1/knowledge/:id` — update, re-embed on title/body change
- [x] `DELETE /v1/knowledge/:id` — soft-delete (`active = false`)
- [x] Wired into `main.py`
- [ ] Knowledge sources CRUD (optional for Phase 2; deferred to follow-on sprint)

### 9. API: channels CRUD

- [x] `GET /v1/channels` — list, filterable by channel_type
- [x] `POST /v1/channels` — create (phone/sms/email/web/whatsapp)
- [x] `GET /v1/channels/:id` — detail
- [x] `PATCH /v1/channels/:id` — update active/config/external_id
- [x] `DELETE /v1/channels/:id` — soft-delete (`active = false`)
- [x] Wired into `main.py`

### 10. API: leads CRUD

- [x] `GET /v1/leads` — paginated, filterable by stage/assigned_to
- [x] `GET /v1/leads/:id` — detail including resolved contact
- [x] `PATCH /v1/leads/:id` — update stage, assigned_to, metadata (auto-sets closed_at on won/lost)
- [x] Wired into `main.py`

### 11. STOP/START/HELP compliance

- [x] In `inbound_normalizer`, after message insert:
  - STOP: sets `contacts.metadata.sms_opt_out = true`, emits `contact.sms_opt_out`, short-circuits (no downstream jobs)
  - START: clears `sms_opt_out`, sets `sms_opt_in_at`, emits `contact.sms_opt_in`
  - HELP: enqueues outbound with compliance-safe message, emits audit event
- [x] Only fires for `message.received` events where body exactly matches keyword set (stop/unsubscribe/cancel/quit/end, start/unstop/subscribe/resume/yes, help/info)
- [x] STOP and HELP short-circuit — no classification or outbound downstream

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
