# Deferred Tasks — Revenue Edge

Comprehensive inventory of every task intentionally deferred during the Phase 1–5
backend implementation. Each item notes its origin phase, why it was deferred, the
implementation cost estimate, and when to revisit.

---

## Phase 2 — After-Hours + FAQ

### D-2.1 Smoke test: `scripts/smoke_phase2.sh` — COMPLETE
- **Completed**: Script seeds business with hours/knowledge, simulates after-hours +
  unanswerable SMS, asserts reply + classification + knowledge_gap task.

### D-2.2 Knowledge sources CRUD
- **Why deferred**: Manual knowledge entry is sufficient for MVP; scraping/parsing adds complexity.
- **Effort**: 4–6 hours (routes + scraper worker)
- **When**: When a business has > 50 knowledge items and wants automated ingestion from their website.

---

## Phase 3 — Qualification + Quote Intake

### D-3.1 Photo request flow (MMS)
- **Why deferred**: Retell's MMS surface may not be stable; photo collection is optional for most verticals.
- **Effort**: 4–8 hours (inbound MMS parsing + storage + `photos` intake field)
- **When**: When a service-based business (e.g., roofing, painting) requests it.
- **Workaround**: The `photo_request` template can include a link to a simple upload page.
- **Implementation notes**:
  - `inbound_normalizer` needs to parse `message.attachments` from Retell webhook
  - Store URLs in `messages.attachments` (JSONB array)
  - `conversation_intelligence` detects attachments → sets `fields_collected.photos`
  - Consider Supabase Storage for self-hosted upload fallback

### D-3.2 Seed default services — COMPLETE
- **Completed**: Added `--services <vertical>` flag to `scripts/seed_business.py`.
  Supports plumbing, hvac, electrical, landscaping, cleaning, dental, general.
  Each vertical has 3–4 service presets with intake fields and price ranges.

### D-3.3 `WorkerSettings.auto_quote_max`
- **Why deferred**: No limit needed for MVP; all quotes go to human review anyway.
- **Effort**: 30 min
- **When**: When a business wants auto-send for quotes under $X.

### D-3.4 Phase 3 metrics additions
- **Why deferred**: The `quotes_sent` column already exists in `metric_snapshots` and is computed by the rollup. Extended metrics (`estimate_turnaround_seconds`, `quote_recovery_wins`) require more complex queries.
- **Effort**: 2 hours
- **When**: After 50+ quotes flow through the system and operators want turnaround KPIs.
- **Implementation**:
  - `estimate_turnaround_seconds`: average time from `lead.created` event to `quote.sent` event per business per day.
  - `quote_recovery_wins`: count leads that moved `quoted → won` after a `quote_followup_*` outbound message.

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

### D-4.3 Google Calendar event updates on reschedule
- **Why deferred**: The `bookings.py` route updates the DB row but doesn't call `google_calendar.update_event` because the API service doesn't have direct access to the worker's Google Calendar library.
- **Effort**: 2 hours (enqueue a `booking-sync` job with `action: reschedule`)
- **When**: After first Google Calendar integration is tested.

### D-4.4 No-show grace period per service
- **Why deferred**: Default 1-hour grace period is hard-coded. Some services (e.g., multi-hour jobs) need longer.
- **Effort**: 30 min
- **When**: When a service business reports false no-show positives.
- **Implementation**: Add `services.metadata.no_show_grace_minutes` and read it in the `no_show_check` handler.

### D-4.5 Phase 4 metrics additions
- **Columns**: `booking_requests`, `callbacks_created`, `no_shows` in `metric_snapshots.payload`.
- **Effort**: 1–2 hours
- **When**: After booking flow is live.

### D-4.6 Smoke test: `scripts/smoke_phase4.sh` — COMPLETE
- **Completed**: Script simulates booking request, asserts either booking created
  or callback fallback (when no GCal connected) + customer notification.

---

## Phase 5 — Reactivation + ROI

### D-5.1 `avg_response_seconds` computation — COMPLETE
- **Completed**: `_avg_response_seconds` in `metrics_rollup.py` computes median
  inbound-to-outbound response time per conversation. Stored in
  `metric_snapshots.payload.avg_response_seconds`. Payload version bumped to 3.

### D-5.2 Enhanced reactivation metrics
- **Metrics**: `reactivation_sent`, `reactivation_replies`, `reactivation_conversions` in `metric_snapshots.payload`.
- **Effort**: 1–2 hours
- **When**: After first reactivation batch.

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

## Priority order for implementation

| Priority | Item | Trigger | Effort |
|---|---|---|---|
| ~~1~~ | ~~D-5.3 Daily summary scheduler~~ | | **DONE** |
| ~~2~~ | ~~D-X.1 Dashboard UI~~ | | **DONE** |
| ~~3~~ | ~~D-X.2 Smoke tests~~ | | **DONE** |
| ~~4~~ | ~~D-4.1 Fuzzy time resolution~~ | | **DONE** |
| ~~5~~ | ~~D-5.1 avg_response_seconds~~ | | **DONE** |
| ~~6~~ | ~~D-X.3 Production Docker config~~ | | **DONE** |
| ~~7~~ | ~~D-X.4 CI/CD pipeline~~ | | **DONE** |
| ~~8~~ | ~~D-3.2 Seed default services~~ | | **DONE** |
| 1 | D-3.1 Photo request (MMS) | Service business requests | 4–8h |
| 2 | D-4.2 Multi-turn slot offering | After basic booking tested | 2h |
| 3 | D-4.3 GCal event updates on reschedule | After GCal integration tested | 2h |
| 4 | D-3.3 auto_quote_max threshold | When auto-send needed | 30m |
| 5 | D-3.4 Extended quote metrics | After 50+ quotes | 2h |
| 6 | D-4.4 No-show grace per service | When false positives reported | 30m |
| 7 | D-4.5 Phase 4 metrics additions | After booking flow live | 1–2h |
| 8 | D-5.2 Enhanced reactivation metrics | After first reactivation batch | 1–2h |
| 9 | D-2.2 Knowledge sources CRUD | When >50 knowledge items | 4–6h |
