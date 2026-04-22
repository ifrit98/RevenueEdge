# Deferred Infrastructure — Revenue Edge

Items intentionally skipped for the MVP but designed for, with implementation
notes so nothing needs to be re-derived. Each section notes when to revisit and
what the trigger condition is.

---

## 1. Per-Contact / Per-Business SMS Rate Limiting

**Status**: Phase 2 deliverable (moved from Phase 1 backlog).
**Trigger**: First real business onboarded.

### Current state

- `contacts.metadata.sms_opt_out` is checked in `outbound_action` (Phase 1)
- No per-contact throttle exists yet

### Implementation plan

- Add `lib/rate_limit.py` in workers:
  - Query `events` table: `SELECT count(*) FROM events WHERE event_type = 'outbound.sms.sent' AND payload->>'contact_id' = :cid AND occurred_at > now() - interval ':N seconds'`
  - Default: 1 SMS per contact per 120 seconds
  - Configurable via `businesses.settings.sms_rate_limit_seconds`
- If limit hit: re-enqueue the outbound job with `available_at = now() + remaining_cooldown`
- Per-business daily cap: `businesses.settings.daily_sms_cap` (default: 500)
  - Query: count today's `outbound.sms.sent` events for the business
  - If hit: create a `tasks.type = 'ops_review'` alerting the operator

### Why not Redis

For MVP volumes (< 100 SMS/day per business), querying `events` is fast enough.
The events table has an index on `(business_id, event_type, occurred_at)`. If
we scale to thousands of SMS per day, add a Redis counter or a materialized
view.

---

## 2. Prometheus / OpenTelemetry Metrics

**Status**: Deferred indefinitely (nice-to-have).
**Trigger**: When we need Grafana dashboards or SLO alerting beyond Sentry.

### Current state

- Sentry captures errors + traces in all services
- `/v1/metrics` serves business-facing daily snapshots
- `/internal/queue/dead-letter/count` serves queue health
- Structured JSON logs capture per-request timing

### Implementation plan

- Port `SMB-MetaPattern/apps/api-gateway/app/prometheus_metrics.py` (already in REUSE_MAP as PILFER)
- Rename counters:
  - `turnkeyclaw_voice_calls_total` → `re_inbound_events_total{event_type=...}`
  - `turnkeyclaw_sms_sent_total` → `re_outbound_sms_total{provider=...}`
  - etc.
- Expose `/metrics` on each service (re-api, re-webhooks, re-workers)
- Scrape with Prometheus + visualize in Grafana
- Key metrics:
  - `re_queue_jobs_claimed_total{queue=..., worker=...}`
  - `re_queue_jobs_completed_total{queue=..., status=succeeded|failed|dead_letter}`
  - `re_queue_lag_seconds{queue=...}` — age of oldest unprocessed job
  - `re_llm_calls_total{model=..., skill=...}`
  - `re_llm_latency_seconds{model=...}`
  - `re_sms_sent_total{provider=retell|twilio|dry_run}`
  - `re_http_requests_total{service=..., method=..., path=..., status=...}`
  - `re_http_request_duration_seconds{service=..., path=...}`

### OpenTelemetry alternative

If we want distributed tracing (trace-ID spans across webhook → API → worker → Supabase):
- Add `opentelemetry-sdk` + `opentelemetry-instrumentation-fastapi` + `opentelemetry-instrumentation-httpx`
- Export to Jaeger or Sentry Performance
- The `X-Trace-ID` header propagation (already in `trace.py`) maps to an OTel trace ID

---

## 3. Model Router Integration

**Status**: Deferred. Detailed plan in [`docs/DEFERRED_MODEL_ROUTER.md`](./DEFERRED_MODEL_ROUTER.md).
**Trigger**: LLM spend > $500/mo, or need for multi-provider routing, or provider outage.

Current approach: direct OpenAI calls from `lib/llm.py` using `Settings.llm_chat_model`.

---

## 4. Voice-First Outbound

**Status**: Deferred to Phase 4–5 timeframe.
**Trigger**: Business requests outbound voice calls (e.g., appointment reminders via voice, warm transfer on handoff).

### Current state

- All outbound is SMS-only
- Retell SDK is installed; `send_sms_retell` already works
- Inbound voice calls are handled (webhook captures missed/ended events)

### Implementation plan

- `providers/voice.py`:
  - `initiate_outbound_call(to_number, from_number, agent_id, metadata)` → Retell Create Phone Call API
  - `transfer_to_human(call_id, transfer_number)` → Retell Transfer Call API
- New `outbound-actions` action: `action = 'call'`
  - Used for: appointment reminders, warm handoff transfers, reactivation calls (requires consent)
- Voice agent configuration:
  - `channels.config.retell_agent_id` — the Retell agent to use for outbound calls
  - `channels.config.voice_greeting` — dynamic first message injected into the agent
- Safety guardrails:
  - Never place outbound voice calls outside business hours
  - Never place voice calls to contacts who have opted out
  - Cap outbound voice calls at N per day per business
  - Log all outbound calls in `events` + `messages`
- TCPA considerations:
  - Outbound voice to mobile requires prior express consent (higher bar than SMS)
  - Must provide opt-out mechanism during the call
  - Document consent chain in `contacts.metadata.voice_consent`

---

## 5. Self-Serve Onboarding

**Status**: Phase 6+ (after the first 10 manually-onboarded businesses prove the model).
**Trigger**: Demand exceeds what manual onboarding can handle.

### Current state

- `scripts/seed_business.py` creates businesses via service-key
- `create_business_with_owner` RPC exists for authenticated users
- No self-serve sign-up flow

### Implementation plan

- **Sign-up flow**:
  1. User creates Supabase Auth account (email + password or Google OAuth)
  2. Onboarding wizard collects:
     - Business name, vertical, timezone
     - Phone number (Retell DID provisioning — may require Retell sub-account)
     - Hours of operation (weekly schedule builder)
     - Services offered (select from vertical presets or custom)
     - First 3 FAQs (inline knowledge creation)
  3. Calls `create_business_with_owner` RPC
  4. Calls `seed_revenue_edge_mvp_defaults` RPC
  5. Creates channel rows for the provisioned DID
  6. Redirects to the dashboard
- **Retell DID provisioning**:
  - Retell API: `POST /v2/phone-number/buy` or `POST /v2/phone-number/import`
  - Must handle: number selection (area code preference), Twilio sub-account, webhook URL registration
  - This is the most complex part of self-serve and may require a dedicated service
- **Stripe billing**:
  - Free trial: 14 days or first 100 conversations
  - Plans: per-business monthly fee + usage-based SMS/voice overages
  - Stripe Checkout for initial payment; Stripe Webhooks for subscription lifecycle
  - Gate features behind plan: `businesses.settings.plan = 'starter' | 'pro' | 'enterprise'`
- **Security**:
  - New businesses start in `status = 'setup'` until onboarding is complete
  - RLS prevents cross-business access from day one
  - Email verification required before enabling outbound messaging

---

## 6. BYOK LLM Keys

**Status**: Deferred indefinitely.
**Trigger**: Enterprise customer requires their own OpenAI/Anthropic account for data isolation or billing.

### Implementation plan

- Add `businesses.settings.openai_api_key_encrypted` (encrypted at rest)
- `lib/llm.py` checks for a per-business key before falling back to the platform key
- Encryption: use Supabase Vault (if available) or `pgcrypto` with a platform-level encryption key
- Audit: log which key was used per LLM call in `events.payload`
- Rate limiting: per-business key has its own rate limits; don't let one business's key exhaust platform capacity

---

## 7. Multi-Location Businesses

**Status**: Schema supports it; not exercised in MVP.
**Trigger**: A business with 2+ locations signs up.

### Current state

- `businesses.service_area` is a JSONB column that can hold multiple regions
- `channels` can have multiple DIDs per business
- `bookings.location` is a JSONB column

### Implementation plan

- Add `locations` table (or use `businesses.service_area` as an array of location objects):
  ```sql
  create table public.locations (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id),
    name text not null,
    address jsonb not null default '{}',
    timezone text not null default 'America/New_York',
    hours jsonb not null default '{}',
    channels uuid[] not null default '{}',  -- channel IDs for this location
    active boolean not null default true
  );
  ```
- Route inbound events to the correct location by `channels → location` mapping
- Each location can have its own hours, timezone, and calendar integration
- Dashboard: location switcher in the sidebar
- Reporting: per-location metric snapshots

---

## 8. Audit Middleware

**Status**: Schema exists (`audit_log` table); middleware not wired.
**Trigger**: First SOC 2 or HIPAA-adjacent requirement.

### Current state

- `SMB-MetaPattern/apps/api-gateway/app/audit.py` exists in REUSE_MAP as PILFER
- `public.audit_log` table is in `schema.sql`

### Implementation plan

- Port `audit.py` as FastAPI middleware
- Log every state-changing API call:
  - Actor (user_id), action (HTTP method + path), target table + target ID, payload summary
- Exclude: read-only calls, health checks, internal-key-only endpoints
- Retention: 90 days default; configurable per-business

---

## 9. Circuit Breaker + External Provider Resilience

**Status**: Deferred.
**Trigger**: Retell or OpenAI outage causes cascading failures.

### Current state

- `lib/llm.py` falls back to heuristic when OpenAI is unreachable
- `providers/sms.py` falls back from Retell to Twilio
- No circuit breaker pattern applied

### Implementation plan

- Port `SMB-MetaPattern/apps/api-gateway/app/circuit_breaker.py` (in REUSE_MAP as PILFER)
- Wrap all external HTTP calls (Retell SMS, Retell voice, OpenAI, Google Calendar, SendGrid) with circuit breaker:
  - Half-open after N consecutive failures
  - Emit `integration_error` event on open
  - Create `tasks.type = 'integration_error'` for operator visibility
- Configurable per-provider thresholds

---

## 10. PII Encryption at Rest

**Status**: `pii_filter.py` exists for log redaction; DB-level encryption deferred.
**Trigger**: HIPAA or financial-services customer.

### Current state

- `contacts.phone_e164` and `contacts.email` are stored in plaintext
- `pii_filter.py` redacts phone/email/SSN patterns from log payloads
- Supabase provides transparent encryption at rest for the entire database

### Implementation plan

- Add application-level field encryption for `contacts.phone_e164`, `contacts.email`, `contacts.name`:
  - Encrypt on write, decrypt on read
  - Use `pgcrypto` or an application-level AES-256-GCM wrapper
  - Store the encryption key in Supabase Vault or an external KMS
  - Port `SMB-MetaPattern/supabase/migrations/0077_contacts_pii_encryption.sql` patterns
- Searching encrypted fields requires either:
  - Blind index (HMAC hash stored alongside for exact match)
  - Decrypt-on-search (expensive, use only for admin/export)

---

## 11. Compliance — TCPA / CAN-SPAM / CCPA

**Status**: Basic opt-out honored; formal compliance deferred.
**Trigger**: Scaling beyond 10 businesses or entering healthcare/finance verticals.

### Current state

- SMS opt-out via `contacts.metadata.sms_opt_out` (Phase 1)
- STOP/START/HELP handling planned for Phase 2
- Retell handles SMS compliance for DIDs it provisions

### Requirements at scale

- **TCPA**:
  - Prior express consent for non-transactional SMS/voice
  - Consent recording: `contacts.metadata.consent_timestamp`, `consent_channel`, `consent_text`
  - Do-not-call list checking (FTC DNC registry)
  - Time-of-day restrictions (already handled by quiet-hours)
- **CAN-SPAM** (email):
  - Unsubscribe link in every marketing email
  - Physical address in email footer
  - Honest subject lines
- **CCPA** (California):
  - Data deletion request handling: `DELETE /v1/contacts/:id` cascade or anonymize
  - Data export: `GET /v1/contacts/:id/export` (JSON)
  - Consent banner if we ever add a web-facing component

---

## Priority order for deferred items

| Priority | Item | Trigger |
|---|---|---|
| 1 | SMS rate limiting | Phase 2 (already planned) |
| 2 | STOP/START/HELP compliance | Phase 2 (already planned) |
| 3 | Audit middleware | First enterprise customer |
| 4 | Circuit breaker | First provider outage |
| 5 | Prom/OTel metrics | When Sentry alone isn't enough |
| 6 | Model router | LLM spend > $500/mo |
| 7 | Voice-first outbound | Business requests it |
| 8 | Self-serve onboarding | Demand > manual capacity |
| 9 | PII encryption | HIPAA/finance customer |
| 10 | BYOK LLM keys | Enterprise data isolation request |
| 11 | Multi-location | Multi-location customer |
| 12 | Full TCPA/CAN-SPAM/CCPA | Legal review before scale |
