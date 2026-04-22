# Deferred Tasks — Revenue Edge

Comprehensive inventory of every task intentionally deferred during the Phase 1–5
backend implementation. Each item notes its origin phase, why it was deferred, the
implementation cost estimate, and when to revisit.

---

## Phase 2 — After-Hours + FAQ

### D-2.1 Smoke test: `scripts/smoke_phase2.sh`
- **Why deferred**: Requires running services against a live Supabase instance; better as a dedicated integration-test sprint.
- **Effort**: 1–2 hours
- **When**: Before first real business onboarding.
- **Spec**: See `PHASE_2_CHECKLIST.md` §13 for the 10-step scenario.

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

### D-3.2 Seed default services
- **Why deferred**: Business-specific; easier via API or a per-vertical seed script.
- **Effort**: 1 hour
- **When**: During onboarding for each new business.
- **Approach**: Extend `scripts/seed_business.py` with a `--services` flag, or add vertical-specific presets (plumbing, HVAC, electrical, etc.) to `seed_mvp_defaults.sql`.

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

### D-3.5 Smoke test: `scripts/smoke_phase3.sh`
- **Why deferred**: Same reasoning as D-2.1.
- **Effort**: 2 hours
- **Spec**: See `PHASE_3_CHECKLIST.md` §10 for the 10-step scenario.

---

## Phase 4 — Booking or Callback

### D-4.1 Fuzzy time resolution
- **Why deferred**: The `booking_worker._resolve_preferred_time` currently only handles ISO datetime strings. Fuzzy inputs like "Thursday morning" or "next week" require NLP or LLM parsing.
- **Effort**: 3–4 hours
- **When**: After the first booking flows are tested with explicit times.
- **Implementation**:
  - Add a small LLM call in `booking_worker` to parse fuzzy times against the business's timezone
  - Or use `dateparser` library for common patterns
  - Map "morning" → 09:00, "afternoon" → 13:00, "evening" → 17:00 as heuristics

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

### D-4.6 Smoke test: `scripts/smoke_phase4.sh`
- **Effort**: 2 hours
- **Spec**: See `PHASE_4_CHECKLIST.md` §9.

---

## Phase 5 — Reactivation + ROI

### D-5.1 `avg_response_seconds` computation
- **Why deferred**: Requires per-conversation join between inbound and outbound messages, which is expensive at scale.
- **Effort**: 2–3 hours
- **When**: After 100+ conversations, when response-time benchmarking becomes meaningful.
- **Implementation**:
  - For each conversation with messages on the metric date:
    - Find earliest inbound message `created_at`
    - Find earliest outbound message `created_at` after it
    - Compute delta in seconds
  - Store the median across all conversations as `avg_response_seconds`

### D-5.2 Enhanced reactivation metrics
- **Metrics**: `reactivation_sent`, `reactivation_replies`, `reactivation_conversions` in `metric_snapshots.payload`.
- **Effort**: 1–2 hours
- **When**: After first reactivation batch.

### D-5.3 Daily summary scheduler integration
- **Why deferred**: The `daily_summary.py` service is complete but not wired into the scheduler. It needs a second scheduled task that runs at end-of-business for each timezone.
- **Effort**: 1 hour
- **When**: Immediately after Phase 5 launch.
- **Implementation**: In `apps/api/app/services/scheduler.py`, add a task that:
  - Runs every hour
  - Checks which businesses have `settings.daily_summary_enabled = true`
  - For each, checks if current UTC time matches their configured EOD hour (default 18:00 local)
  - Calls `send_daily_summary(business_id, date.today())`

### D-5.4 Smoke test: `scripts/smoke_phase5.sh`
- **Effort**: 2–3 hours
- **Spec**: See `PHASE_5_CHECKLIST.md` §7.

---

## Cross-Phase Infrastructure

### D-X.1 Dashboard UI (Next.js)
- **Status**: API endpoints are complete for all 5 phases. No frontend code exists yet.
- **Spec**: See `docs/DASHBOARD_SPEC.md` for full page-by-page specification.
- **Effort**: 40–60 hours for a full dashboard
- **When**: After backend is stabilized with real traffic.
- **Stack**: Next.js 15 + Supabase Auth + Tailwind CSS + shadcn/ui
- **Port source**: `SMB-MetaPattern/apps/dashboard/` for auth shell and layout patterns.

### D-X.2 End-to-end integration tests
- **Status**: All smoke test scripts are specified but not implemented.
- **Effort**: 8–12 hours for all phases
- **When**: Before first production deployment.
- **Approach**: Python scripts that use `httpx` to hit the API, seed data, simulate webhooks, and assert database state via Supabase client.

### D-X.3 Docker Compose production config
- **Status**: `docker-compose.yml` defines dev services. Production needs:
  - Health check readiness probes
  - Resource limits
  - Secret injection (not env files)
  - Log driver configuration
  - Caddy reverse proxy for TLS
- **Effort**: 4–6 hours
- **When**: Before first deployment to a real server.

### D-X.4 CI/CD pipeline
- **Status**: Not started.
- **Effort**: 4 hours
- **Implementation**: GitHub Actions with:
  - `py_compile` syntax check on all Python files
  - `pytest` unit tests (when written)
  - Docker build validation
  - Auto-deploy to staging on push to `main`

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
| 1 | D-5.3 Daily summary scheduler | Phase 5 launch | 1h |
| 2 | D-X.2 Smoke tests (all phases) | Before production | 8–12h |
| 3 | D-X.1 Dashboard UI | After backend stable | 40–60h |
| 4 | D-4.1 Fuzzy time resolution | After first bookings | 3–4h |
| 5 | D-3.1 Photo request (MMS) | Service business requests | 4–8h |
| 6 | D-5.1 avg_response_seconds | After 100+ conversations | 2–3h |
| 7 | D-X.3 Production Docker config | Before deployment | 4–6h |
| 8 | D-X.4 CI/CD pipeline | Before deployment | 4h |
| 9 | D-3.4 Extended quote metrics | After 50+ quotes | 2h |
| 10 | D-3.2 Seed default services | Per-business onboarding | 1h |
