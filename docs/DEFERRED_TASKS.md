# Deferred Tasks — Revenue Edge

Comprehensive inventory of every task intentionally deferred during the Phase 1–5
backend implementation. Each item notes its origin phase, why it was deferred, the
implementation cost estimate, and when to revisit.

---

## Phase 2 — After-Hours + FAQ

### D-2.1 Smoke test: `scripts/smoke_phase2.sh` — COMPLETE
- **Completed**: Script seeds business with hours/knowledge, simulates after-hours +
  unanswerable SMS, asserts reply + classification + knowledge_gap task.

### D-2.2 Knowledge sources CRUD — COMPLETE
- **Completed**: Full knowledge ingestion pipeline implemented with three modes:
  - Website scraper (`POST /v1/knowledge/ingest/website`) with SSRF protection
  - Document upload (`POST /v1/knowledge/ingest/document`) for PDF/DOCX/TXT
  - Google Docs integration (`POST /v1/knowledge/ingest/google-docs`)
- Workers: `knowledge_ingestion` handles scrape, embed, and Google Docs fetch actions.
- Libraries: `web_scraper.py`, `doc_parser.py`, `text_chunker.py`.

---

## Phase 3 — Qualification + Quote Intake

### D-3.1 Photo request flow (MMS) — COMPLETE
- **Completed**: Dual-mode implementation:
  - **Retell MMS primary**: Webhook model extended with `attachments` and `media_urls` fields;
    `inbound_normalizer` extracts attachment URLs from multiple payload formats.
  - **Upload-link fallback**: `POST /v1/uploads/request-link` creates a one-time upload token;
    `outbound_action` sends the link via SMS; customer uploads via Supabase Storage.
  - LLM prompt includes `request_photo` next-action guidance.
  - Migration `0003_upload_tokens.sql` adds `upload_tokens` table and `photos` bucket.

### D-3.2 Seed default services — COMPLETE
- **Completed**: Added `--services <vertical>` flag to `scripts/seed_business.py`.
  Supports plumbing, hvac, electrical, landscaping, cleaning, dental, general.
  Each vertical has 3–4 service presets with intake fields and price ranges.

### D-3.3 `WorkerSettings.auto_quote_max` — COMPLETE
- **Completed**: `quote_drafting` worker checks `businesses.settings.auto_quote_max`.
  Quotes at or below the threshold are auto-approved and enqueued for send, bypassing manual review.

### D-3.4 Phase 3 metrics additions — COMPLETE
- **Completed**: `metrics_rollup.py` now computes `estimate_turnaround_seconds` and
  `quote_recovery_wins` and stores them in `metric_snapshots.payload` (version 4).

### D-3.5 Smoke test: `scripts/smoke_phase3.sh` — COMPLETE
- **Completed**: Script seeds service, simulates quote request + field replies, asserts
  quote draft + approval + send + follow-up scheduling.

---

## Phase 4 — Booking or Callback

### D-4.1 Fuzzy time resolution — COMPLETE
- **Completed**: `_resolve_preferred_time` in `booking_worker.py` now handles day names
  (Monday–Sunday), relative terms (today, tomorrow, next week), and time-of-day
  (morning→09, afternoon→13, evening→17, etc.) resolved against the business timezone.

### D-4.2 Multi-turn slot offering
- **Why deferred**: The slot-offering SMS is generated; customer reply parsing relies on the existing `conversation_intelligence` re-classification loop.
- **Effort**: 2 hours to add explicit "customer selected slot N" detection
- **When**: After testing the basic booking flow.

### D-4.3 Google Calendar event updates on reschedule — COMPLETE
- **Completed**: `bookings.py` cancel and reschedule endpoints enqueue `booking-sync`
  jobs with `action: cancel` or `action: reschedule`. The `booking_worker` dispatches
  to `_handle_cancel` (calls `cancel_event`) and `_handle_reschedule` (calls `update_event`
  with proper Google Calendar event format).

### D-4.4 No-show grace period per service — COMPLETE
- **Completed**: `booking_worker` reads `services.metadata.no_show_grace_minutes` (defaults to 60)
  and uses it when calculating the `no_show_at` timestamp.

### D-4.5 Phase 4 metrics additions — COMPLETE
- **Completed**: `metrics_rollup.py` now computes `booking_requests`, `callbacks_created`,
  and `no_shows` and stores them in `metric_snapshots.payload` (version 4).

### D-4.6 Smoke test: `scripts/smoke_phase4.sh` — COMPLETE
- **Completed**: Script simulates booking request, asserts either booking created
  or callback fallback (when no GCal connected) + customer notification.

---

## Phase 5 — Reactivation + ROI

### D-5.1 `avg_response_seconds` computation — COMPLETE
- **Completed**: `_avg_response_seconds` in `metrics_rollup.py` computes median
  inbound-to-outbound response time per conversation. Stored in
  `metric_snapshots.payload.avg_response_seconds`. Payload version bumped to 3.

### D-5.2 Enhanced reactivation metrics — COMPLETE
- **Completed**: `metrics_rollup.py` now computes `reactivation_sent`, `reactivation_replies`,
  and `reactivation_conversions` and stores them in `metric_snapshots.payload` (version 4).

### D-5.3 Daily summary scheduler integration — COMPLETE
- **Completed**: Wired `_summary_loop` into `apps/api/app/services/scheduler.py`.
  Checks hourly, sends once per day after 22:00 UTC via `run_daily_summaries()`.

### D-5.4 Smoke test: `scripts/smoke_phase5.sh` — COMPLETE
- **Completed**: Script seeds stale leads, calls reactivation preview/launch,
  tests metrics comparison + rollup endpoints.

---

## Cross-Phase Infrastructure

### D-X.1 Dashboard UI (Next.js) — COMPLETE
- **Completed**: Full Next.js 16 dashboard with 12 pages (dashboard home, inbox,
  leads, quotes, bookings, knowledge, reactivation, 4 settings pages), Supabase
  SSR auth (login/signup), sidebar nav, StatusBadge component, Recharts charts.
  Build passes cleanly. Committed as `272fa1a`.

### D-X.2 End-to-end integration tests — MOSTLY COMPLETE
- **Status**: Smoke test scripts implemented for all phases (0–5). Each script
  seeds data, simulates webhooks, and asserts database state via Supabase client.
  Full `httpx`-based integration test suite remains optional enhancement.
- **Remaining**: True E2E tests that start all services in Docker.

### D-X.3 Docker Compose production config — COMPLETE
- **Completed**: `docker-compose.prod.yml` with Caddy TLS proxy, health checks,
  resource limits, JSON log drivers, env-based secret injection, and standalone
  dashboard Dockerfile. Caddyfile at `infra/caddy/Caddyfile`.

### D-X.4 CI/CD pipeline — COMPLETE
- **Completed**: `.github/workflows/ci.yml` with:
  - Python syntax + import check for api/webhooks/workers
  - Dashboard TypeScript check + Next.js build
  - Docker build validation for all services
  - ShellCheck for smoke scripts
  - Concurrency groups to cancel stale runs

---

## Already Documented Separately

These items have their own detailed documents and are not repeated here:

| Item | Document | Status |
|---|---|---|
| Model Router | `docs/DEFERRED_MODEL_ROUTER.md` | Detailed plan ready |
| Prometheus / OTel | `docs/DEFERRED_INFRASTRUCTURE.md` §2 | Deferred |
| Voice-First Outbound | `docs/DEFERRED_INFRASTRUCTURE.md` §4 | Deferred |
| Self-Serve Onboarding | `docs/DEFERRED_INFRASTRUCTURE.md` §5 | Phase 6+ |
| BYOK LLM Keys | `docs/DEFERRED_INFRASTRUCTURE.md` §6 | Deferred |
| Multi-Location | `docs/DEFERRED_INFRASTRUCTURE.md` §7 | Schema ready |
| Audit Middleware | `docs/DEFERRED_INFRASTRUCTURE.md` §8 | Schema ready |
| Circuit Breaker | `docs/DEFERRED_INFRASTRUCTURE.md` §9 | Deferred |
| PII Encryption | `docs/DEFERRED_INFRASTRUCTURE.md` §10 | Deferred |
| TCPA/CAN-SPAM/CCPA | `docs/DEFERRED_INFRASTRUCTURE.md` §11 | Basic compliance done |

---

## External Service Hardening — COMPLETE

A comprehensive audit of all external service integrations was performed, resulting
in 17 fixes across security, data correctness, and resilience. Applied as migration
`0004_hardening.sql` and code changes across 16 files. Committed as `961fe55`.

Key fixes:
- SSRF protection in web scraper (private-IP blocklist, queue cap, response size limit)
- Booking reschedule argument fix (`update_event` kwargs -> dict)
- `PermanentError` now immediately dead-letters via `p_force_dead` SQL parameter
- Contact upsert race condition handling
- Atomic conversation metadata merge via SQL RPC
- `CalendarUnavailableError` prevents wrong slot offers during outages
- JSON decode, transport, and timeout error handling across all providers
- Stuck running job reaper (every 2 minutes)
- RPC timeouts (30s) on all Supabase calls from workers
- Per-contact SMS rate limiting
- Google Doc ID injection prevention
- Document parse error wrapping
- Webhook UTF-8 decode safety

---

## Priority Order for Remaining Work

| Priority | Item | Trigger | Effort |
|---|---|---|---|
| 1 | D-4.2 Multi-turn slot offering | After basic booking tested | 2h |

All other deferred tasks have been completed. See individual sections above for details.

### Completed Task Summary

| Item | Commit |
|---|---|
| D-2.1 Smoke phase 2 | `dbfa562` |
| D-2.2 Knowledge ingestion | `80a077d` |
| D-3.1 Photo/MMS flow | `80a077d` |
| D-3.2 Seed services | `dbfa562` |
| D-3.3 auto_quote_max | `7cb074a` |
| D-3.4 Quote metrics | `7cb074a` |
| D-3.5 Smoke phase 3 | `dbfa562` |
| D-4.1 Fuzzy time | `7cb074a` |
| D-4.3 GCal reschedule/cancel | `7cb074a` |
| D-4.4 No-show grace | `7cb074a` |
| D-4.5 Phase 4 metrics | `7cb074a` |
| D-4.6 Smoke phase 4 | `dbfa562` |
| D-5.1 avg_response_seconds | `7cb074a` |
| D-5.2 Reactivation metrics | `7cb074a` |
| D-5.3 Daily summary | `272fa1a` |
| D-5.4 Smoke phase 5 | `dbfa562` |
| D-X.1 Dashboard | `272fa1a` |
| D-X.2 Smoke tests | `dbfa562` |
| D-X.3 Prod Docker | `dbfa562` |
| D-X.4 CI/CD | `dbfa562` |
| Hardening (17 fixes) | `961fe55` |
