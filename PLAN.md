# Revenue Edge Agent — Implementation Plan

**Status:** Draft v1
**Date:** 2026-04-21
**Path note:** Written to `/root/AlchemyAI/RevenueEdge/`. If you actually want `/root/AlchemicalAI/RevenueEdge/` (separate tree from `AlchemyAI/`), flag and I'll relocate.

Companion documents in this directory:

- [`REUSE_MAP.md`](./REUSE_MAP.md) — file-by-file inventory of what to pilfer from `SMB-MetaPattern` and where it lands here.
- [`PHASE_1_CHECKLIST.md`](./PHASE_1_CHECKLIST.md) — concrete first-sprint tasks.
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — target topology for the MVP.

---

## 1. The question, and the honest answer

> Given `SMB-MetaPattern` as starter code and `revenue-edge-agent-pack/` as the MVP spec, do we adapt or start from scratch?

**Recommendation: hybrid fork.** Create a new repo at `/root/AlchemyAI/RevenueEdge/` that **inherits infrastructure bones** from `SMB-MetaPattern` but **adopts the pack's schema and workflow contract as canonical**. Do *not* branch or work inside `SMB-MetaPattern` itself — it is a mature real-estate-vertical product with 79 Supabase migrations, 59 OpenClaw skills, CMA/MLS/loan subsystems, and per-tenant Docker orchestration that would fight the generic SMB thesis.

### Why not "start from scratch"

`SMB-MetaPattern` has already solved, at production quality, about 60% of what Revenue Edge needs at the infrastructure layer:

- Supabase JWT + internal-service-key auth with 5-minute tenant cache (`apps/api-gateway/app/auth.py`)
- Row Level Security patterns that match the pack's multi-tenant policy model
- Retell AI voice + SMS integration with webhook signature verification, post-call analysis, transcript extraction, missed-call handling (`webhooks_retell.py`, `retell_inbound.py`, `retell_events.py`, `services/retell_service.py`, `services/retell_post_call.py`)
- Twilio SMS webhook + STOP/HELP/START compliance (`webhooks_twilio.py`)
- A Node.js Supabase-polled worker with `lock_jobs_rpc` that is 80% shaped like the pack's `claim_queue_jobs` pattern (`workers/campaign_worker.ts`)
- An email provider adapter with SendGrid (`providers/email.py`) and an SMS adapter with Retell + Twilio fallback (`providers/sms.py`)
- A multi-provider LLM router with skill→tier policy, sticky sessions, and cost tracking (`apps/model-router/`)
- Rate limiting, circuit breaker, audit middleware, PII encryption helpers, scheduler, error handling, Prometheus metrics, Sentry wiring
- OAuth refresh (Google + HubSpot) with encrypted token storage
- A Next.js 15 dashboard shell with Supabase auth + admin console + observability

Rebuilding this from zero costs ~6–10 engineer-weeks and produces a worse version than what already exists.

### Why not "fork and retheme SMB-MetaPattern"

The cost of keeping the real-estate baggage is high:

- 79 migrations with RE-specific tables (`listings`, `cma_reports`, `loans`, `market_data_sources`, `mls_*`, `heartbeat_configs`, `agent_profiles`, `taylor_universal_programs`, `jordan_nurture`, `avery_lead_qualification`, `skye_service_requests`, `harper_scheduling`, `sphere_pulse`, `docusign_envelopes`, `gmail_priority_and_recap`, `transactions`, `listing_launch`, etc.). The pack's clean 20-table schema is a better canonical model.
- 59 OpenClaw skills and a 2GB-memory OpenClaw gateway runtime — far too heavy for an MVP that just needs to answer SMS and drop a task on a queue.
- Per-tenant Docker stacks via `tenant-api` with per-tenant Caddy route injection — useful eventually, not for a 1-business pilot.
- Tenant model uses `tenants` + `tenant_users` + `current_setting('app.current_tenant_id')` RLS. The pack uses `businesses` + `business_members` + `auth.uid()` RLS helpers. These are semantically equivalent but the pack's version is cleaner and already shipped.
- Pipeline v2 service (`services/pipeline_service.py`, 1,718 lines) encodes a real-estate buyer/seller pipeline that does not map to quote/book/follow-up.

**Net:** we'd spend more time ripping out than we'd save reusing.

### The hybrid fork: what we actually do

1. **New repo root** at `/root/AlchemyAI/RevenueEdge/`.
2. **Adopt the pack's schema verbatim** as the canonical data model. This is a decisive reset: we use `businesses` not `tenants`, `leads` not `contacts.pipeline_stage`, `knowledge_items` not scattered RE prompt files, `queue_jobs` not `jobs`.
3. **Port (not copy) a short list of infrastructure modules** from `SMB-MetaPattern`, rewriting the parts that reference RE tables or the old tenant model. See [`REUSE_MAP.md`](./REUSE_MAP.md) for the exact file list.
4. **Write new, thin workers** against the pack's `claim_queue_jobs` / `fail_queue_job` / `complete_queue_job` RPCs rather than `lock_jobs_rpc`. The campaign_worker is a good structural reference but we want new code.
5. **Minimal dashboard** — a stripped Next.js app with auth, operator inbox, knowledge editor, business settings, and ROI dashboard. No CMA, no MLS, no listings, no loans.
6. **Defer OpenClaw / skills runtime entirely.** The MVP's "intelligence" is a single `conversation_intelligence_worker` that calls an LLM with a grounded prompt and returns the pack's decision object. Revisit agentic skills only after the five MVP workers are live and the first missed-call-recovery wedge is shipping.

---

## 2. Target topology

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Revenue Edge MVP                              │
│                                                                      │
│  Caddy (host)   ──TLS──┐                                             │
│                        ├─► re-api (FastAPI :8080)                    │
│                        ├─► re-dashboard (Next.js :3000)              │
│                        └─► re-webhooks (FastAPI :8081)               │
│                                                                      │
│  re-api ─┬─► Supabase (Postgres + pgvector + Auth + Storage)         │
│          ├─► Retell AI (phone + SMS)                                 │
│          ├─► SendGrid / Mailgun (email send)                         │
│          ├─► Google Calendar (optional booking sync)                 │
│          └─► OpenAI / Anthropic (via re-router)                      │
│                                                                      │
│  re-webhooks ─► writes queue_jobs(inbound-events) in Supabase        │
│                                                                      │
│  re-router (:8090) ─► multi-provider LLM proxy with policy           │
│                                                                      │
│  re-workers (Node.js or Python) ─► poll claim_queue_jobs RPC         │
│     │                                                                │
│     ├── inbound_normalizer                                           │
│     ├── conversation_intelligence_worker                             │
│     ├── outbound_action_worker                                       │
│     ├── handoff_worker                                               │
│     └── followup_scheduler_worker                                    │
│                                                                      │
│  re-rollup (cron) ─► metric_snapshots daily                          │
└──────────────────────────────────────────────────────────────────────┘
```

Services (all containerized via a new `docker-compose.yml` modeled on `SMB-MetaPattern/docker-compose.yml`, with the RE-specific containers removed):

| Service          | Role                                              | Port (loopback) | Memory |
|------------------|---------------------------------------------------|-----------------|--------|
| `re-api`         | FastAPI: REST surface for dashboard + operators   | 8080            | 512m   |
| `re-webhooks`    | Inbound webhook receiver (Retell, Twilio, email)  | 8081            | 256m   |
| `re-dashboard`   | Next.js 15 operator console                       | 3000            | 512m   |
| `re-router`      | Model router proxy                                | 8090            | 256m   |
| `re-workers`     | Queue consumer (polls `claim_queue_jobs` RPC)     | —               | 512m   |
| `re-rollup`      | Cron-like scheduler for daily metric rollups      | —               | 128m   |

No OpenClaw, no tenant-api, no Grafana (rely on Sentry + `/metrics` endpoint + Supabase logs for MVP).

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for details.

---

## 3. Repo layout

```
/root/AlchemyAI/RevenueEdge/
├── README.md
├── PLAN.md                         ← this file
├── REUSE_MAP.md                    ← source → dest file mapping
├── PHASE_1_CHECKLIST.md            ← concrete first-sprint tasks
├── ARCHITECTURE.md                 ← topology, data flow, boundaries
├── SKILL.md                        ← copy of pack SKILL.md (canonical agent spec)
├── .env.example
├── docker-compose.yml
├── supabase/
│   ├── config.toml
│   ├── schema.sql                  ← from pack
│   ├── seed_mvp_defaults.sql       ← from pack
│   └── migrations/
│       └── 00000000000000_initial.sql   ← = schema.sql for Supabase CLI
├── workflows/
│   ├── queue_workflow_pack.yaml    ← from pack (canonical workflow contract)
│   └── QUEUE_WORKFLOWS.md          ← from pack
├── apps/
│   ├── api/                        ← FastAPI: CRUD + internal endpoints
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── auth.py              (ported)
│   │   │   ├── db.py                (ported)
│   │   │   ├── config.py            (ported)
│   │   │   ├── providers/
│   │   │   │   ├── sms.py           (ported, Retell+Twilio)
│   │   │   │   └── email.py         (ported, SendGrid)
│   │   │   ├── api/
│   │   │   │   ├── businesses.py
│   │   │   │   ├── channels.py
│   │   │   │   ├── contacts.py
│   │   │   │   ├── conversations.py
│   │   │   │   ├── leads.py
│   │   │   │   ├── quotes.py
│   │   │   │   ├── bookings.py
│   │   │   │   ├── tasks.py
│   │   │   │   ├── knowledge.py
│   │   │   │   ├── metrics.py
│   │   │   │   └── internal.py      ← queue admin + service-role ops
│   │   │   ├── services/
│   │   │   │   ├── decision_engine.py       ← new: produces decision object
│   │   │   │   ├── knowledge_retrieval.py   ← pgvector + lexical
│   │   │   │   ├── intake_extractor.py      ← LLM → intake_fields
│   │   │   │   ├── template_renderer.py     (ported)
│   │   │   │   └── metrics_rollup.py
│   │   │   ├── middleware/
│   │   │   │   ├── audit.py         (ported)
│   │   │   │   ├── rate_limit.py    (ported, simplified)
│   │   │   │   └── circuit_breaker.py (ported)
│   │   │   └── monitoring.py        (ported)
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── webhooks/                   ← FastAPI: signature verify + enqueue
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── retell.py            (heavily ported from webhooks_retell.py)
│   │   │   ├── twilio.py            (heavily ported from webhooks_twilio.py)
│   │   │   ├── email_inbound.py     ← new: SendGrid inbound parse or IMAP
│   │   │   └── web_form.py          ← new: POST /web-form
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── workers/                    ← Node.js TS workers (pattern from campaign_worker.ts)
│   │   ├── src/
│   │   │   ├── lib/
│   │   │   │   ├── supabase.ts
│   │   │   │   ├── queue.ts         ← claim/complete/fail helpers
│   │   │   │   ├── logger.ts        (ported)
│   │   │   │   └── providers.ts     ← api-gateway client
│   │   │   ├── workers/
│   │   │   │   ├── inbound_normalizer.ts
│   │   │   │   ├── conversation_intelligence.ts
│   │   │   │   ├── outbound_action.ts
│   │   │   │   ├── handoff.ts
│   │   │   │   └── followup_scheduler.ts
│   │   │   └── index.ts              ← dispatches by queue_name
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── Dockerfile
│   ├── router/                     ← model router (pilfered wholesale)
│   │   └── (from apps/model-router, minimal edits)
│   └── dashboard/                  ← Next.js 15
│       ├── app/
│       │   ├── (auth)/…
│       │   ├── (app)/
│       │   │   ├── inbox/          ← handoff task queue
│       │   │   ├── conversations/  ← conversation viewer
│       │   │   ├── leads/
│       │   │   ├── quotes/         ← quote review & approval
│       │   │   ├── knowledge/      ← knowledge_items CRUD + approve
│       │   │   ├── settings/       ← business profile, rules, channels, hours
│       │   │   └── dashboard/      ← ROI metrics
│       │   └── api/                ← BFF routes proxying to re-api
│       ├── package.json
│       └── Dockerfile
├── infra/
│   ├── caddy/Caddyfile             (ported, simplified)
│   └── docker-compose.yml
└── scripts/
    ├── seed_business.sh
    ├── run_migrations.sh
    └── smoke_test.sh
```

---

## 4. What we pilfer, what we rewrite, what we skip

Full table in [`REUSE_MAP.md`](./REUSE_MAP.md). Summary:

### Pilfer wholesale (minor edits)

- `apps/model-router/` → `apps/router/` — change `policy.json` skill names to `revenue_edge` tiers (fast, balanced, frontier) but keep engine intact
- `apps/api-gateway/app/auth.py` → `apps/api/app/auth.py` — rename `tenants`/`tenant_users` queries to `businesses`/`business_members`, switch from custom cache to Supabase RLS helper call
- `apps/api-gateway/app/db.py` → `apps/api/app/db.py` — unchanged
- `apps/api-gateway/app/config.py` → `apps/api/app/config.py` — trim RE-specific env vars
- `apps/api-gateway/app/providers/sms.py` → `apps/api/app/providers/sms.py` — unchanged
- `apps/api-gateway/app/providers/email.py` → `apps/api/app/providers/email.py` — unchanged
- `apps/api-gateway/app/audit.py`, `rate_limit.py`, `circuit_breaker.py`, `error_handling.py`, `monitoring.py` → `apps/api/app/middleware/` — unchanged
- `apps/api-gateway/app/utils/phone.py`, `templates.py` → `apps/api/app/utils/`
- `workers/logger.ts` → `apps/workers/src/lib/logger.ts` — unchanged
- Retell webhook signature verification logic from `webhooks_retell.py` (lines ~100–200) → `apps/webhooks/app/retell.py`
- Twilio STOP/HELP/START + signature verification from `webhooks_twilio.py` → `apps/webhooks/app/twilio.py`
- Infra patterns: `infra/caddy/Caddyfile` (simplified), `docker-compose.yml` (trimmed), `scripts/deploy-hetzner.sh`, Sentry config

### Port with structural change

- `workers/campaign_worker.ts` → serves as **reference pattern** for `apps/workers/src/`. We rewrite fresh against `claim_queue_jobs` RPC and the pack's workflow pack. The SIGTERM graceful-shutdown, Sentry wiring, and `internalGatewayHeaders` helper all transfer as patterns.
- `services/retell_post_call.py` extraction logic → becomes reference for `conversation_intelligence` worker's field-extraction prompt. The agent-specific `_process_*` methods (Kelly/Avery/Jordan/Skye) do not transfer — they are RE-specific. But the *pattern* of "fetch call → parse transcript → extract `collected_dynamic_variables` → upsert contact → queue notification" transfers directly.
- `retell_inbound.py`'s phone-number → tenant lookup → becomes channel lookup in `apps/webhooks/app/retell.py` (but against `channels` table, not `phone_numbers`).
- Next.js dashboard shell, auth middleware, and admin console page → `apps/dashboard/app/` with all RE-specific routes stripped.
- `supabase/migrations/0005_job_queue.sql` + `0006_job_lock_fn.sql` — already superseded by the pack's `queue_jobs` + `claim_queue_jobs`, **do not port**. Use pack schema as-is.

### Skip entirely (MVP)

- OpenClaw gateway (`turnkeyclaw/`) — too heavy. The MVP intelligence worker is a direct LLM call with grounded retrieval, not an agent runtime. Reconsider after MVP.
- `tenant-api` + per-tenant Docker stacks — MVP is single-container-per-service multi-tenant via RLS. Isolation-per-tenant is a v2 concern.
- All 59 OpenClaw skills — RE-specific.
- CMA / listings / loans / market-data / memory / soul / heartbeat / morning-briefing services — all RE-specific.
- `services/pipeline_service.py` (1,718 lines) — RE-specific (buyer/seller pipelines, Zillow-style property matching).
- HubSpot / FollowUpBoss webhooks — defer. The MVP ships with Retell + Twilio + SendGrid + web-form. CRM sync is a post-MVP integration point.
- `apps/api-gateway/app/services/memory_service.py` (2,154 lines) — RE-agent episodic memory. Knowledge layer in the pack (`knowledge_items` + pgvector) is the canonical replacement.
- `apps/api-gateway/app/skills/` — OpenClaw skill definitions.
- `apps/api-gateway/app/agents/`, `crew/`, `prompt/`, `prompts/`, `crm/` subtrees — agent framework code.

### Undecided (hold pending user input)

- **Language for `apps/workers/`.** `SMB-MetaPattern` used TypeScript for the campaign worker. The rest of the pack runtime is Python. Pick one. My default: **Python** for the MVP — it co-locates with the webhook handlers that already do Retell transcript parsing and lets us share the provider clients. TypeScript is viable if we want the `retell-sdk` first-class; pack currently assumes we can call the Retell REST API directly from Python via `httpx`, which is already demonstrated in `services/retell_service.py`.
- **Webhook service split.** `SMB-MetaPattern` runs a separate `smb-webhooks` (Node) + `smb-webhook-service` (Python) pair. The pack's pattern is cleaner as a single FastAPI service. Recommend consolidating to a single `re-webhooks` Python service.
- **Keep/ditch model-router.** The router adds real value only when we have multiple providers and policy-based routing. For MVP with one provider (OpenAI), direct `httpx` calls are simpler. Recommend **skip for MVP**, add when we need per-business BYOK or multi-provider fallback.

---

## 5. Phased implementation plan

Each phase is designed to end with a shippable state, not a half-built component.

### Phase 0 — Scaffolding (1–2 days)

Goal: repo exists, Supabase project exists, `hello world` round-trip works.

- [ ] Create `/root/AlchemyAI/RevenueEdge/` with structure above
- [ ] Copy pack files into `supabase/`, `workflows/`, and `SKILL.md`
- [ ] Create Supabase project, run `supabase/schema.sql`
- [ ] Scaffold `apps/api/` with FastAPI + `auth.py`, `db.py`, `config.py`, a `/ready` endpoint
- [ ] Scaffold `apps/workers/` with a skeleton polling loop that claims from `queue_jobs`, logs the payload, marks complete
- [ ] Scaffold `apps/webhooks/` with a `/healthz` endpoint
- [ ] Scaffold `apps/dashboard/` with Supabase auth and a single "Businesses" page that lists businesses the logged-in user is a member of
- [ ] `docker-compose.yml` brings up all four services + Supabase local (optional)
- [ ] `scripts/seed_business.sh` creates a test business via `public.create_business_with_owner` and optionally runs `public.seed_revenue_edge_mvp_defaults`

**Exit criteria:** log in to dashboard, see a business, enqueue a dummy job via SQL, watch the worker print it and mark it complete.

### Phase 1 — Missed-call recovery end-to-end (1–2 weeks)

Goal: the wedge offer works. Missed Retell call arrives → AI texts back within seconds → conversation is logged → operator sees it in the inbox.

- [ ] Port Retell webhook signature verification into `apps/webhooks/app/retell.py`
- [ ] Implement `call.missed` → enqueue `inbound-events` job with idempotency key `inbound:retell:{provider_call_id}` (per pack spec)
- [ ] Implement `inbound_normalizer` worker: upsert `contacts` by phone, create `conversations`, insert `messages` for missed-call marker, enqueue `conversation-intelligence` (plus a direct `outbound-actions` job for the textback template since missed-call recovery has a known template)
- [ ] Implement `outbound_action_worker` for SMS: uses `providers/sms.py` (Retell SMS send), respects quiet hours from `workflow_defaults`, emits `message.sent` event, records in `messages`
- [ ] Implement a minimal `conversation_intelligence_worker`:
  - Load business profile, active services, approved knowledge
  - Call LLM with the pack's SKILL.md system prompt + decision-object JSON schema
  - Persist decision via `events` insert + `conversations.current_intent`/`urgency` update
  - Enqueue next action queue based on `recommended_next_action`
- [ ] Dashboard pages:
  - `Settings → Business` (name, vertical, timezone, hours, service area)
  - `Settings → Channels` (attach Retell phone number → `channels` row)
  - `Settings → Knowledge` (FAQ CRUD with approval toggle)
  - `Inbox` (open `conversations` with last message preview, status filter)
  - `Conversation detail` (message thread, decision object, lead/intake fields)
  - `Dashboard` (missed_calls, missed_calls_recovered, avg_first_response_seconds from `metric_snapshots`)
- [ ] `metrics_rollup` cron worker (runs hourly + EOD) → aggregates into `metric_snapshots`
- [ ] Smoke-test script that places a Retell test call, verifies textback fires, verifies metric increments

**Exit criteria:** from a fresh business, a real missed call from a real phone triggers a real textback within 30 seconds and shows up in the operator inbox with the transcript summary.

### Phase 2 — After-hours intake + FAQ-to-conversion (1 week)

Goal: inbound SMS works outside hours; FAQs are grounded in approved knowledge.

- [ ] Implement `message.received` handler for inbound SMS (Retell + Twilio fallback)
- [ ] Quiet-hours logic using `businesses.hours` + timezone
- [ ] `after_hours_intake` workflow in the intelligence worker
- [ ] `faq_to_conversion` workflow: knowledge retrieval via pgvector cosine + lexical fallback (use `knowledge_items.embedding` HNSW index from pack schema)
- [ ] Embedding generation on knowledge_item create/update (`knowledge-ingestion` queue; single worker)
- [ ] `handoff_worker` — creates `tasks` of type `knowledge_review` when a question hits the "knowledge missing" fallback
- [ ] Dashboard: `Inbox → Handoffs` view showing `tasks.priority<=2`

**Exit criteria:** business owner uploads 10 FAQ items, marks them approved, and a real inbound "what are your hours" SMS gets a grounded answer + buying-intent follow-up question.

### Phase 3 — Qualification + quote intake (1–2 weeks)

Goal: `quote_request` conversations collect required fields and create a `quote_review` task.

- [ ] `intake_extractor` service with structured extraction prompt (name, phone, email, service, address, scope, urgency)
- [ ] `leads` + `intake_fields` lifecycle: `new → contacted → qualified → awaiting_quote`
- [ ] Photo request flow via MMS (Retell) or SMS-with-link fallback
- [ ] `quote_drafting_worker` — renders `message_templates.quote_template` with intake fields into `quotes.draft_text`, status `awaiting_review`
- [ ] Dashboard `Quotes` page — operator reviews, edits, approves, one-click send
- [ ] `quote.sent` event → `outbound_action_worker` sends
- [ ] `quote_recovery_followup` workflow with 3-attempt cadence (day 2, day 4, manual)

**Exit criteria:** operator reviews a draft quote < 5 minutes after an inbound inquiry, approves with one click, and the system tracks follow-up until closure.

### Phase 4 — Booking or callback (1 week)

Goal: when a business has calendar integration, autopilot books qualified appointments within rules.

- [ ] Google Calendar OAuth (port from `SMB-MetaPattern/apps/api-gateway/app/oauth_refresh.py`)
- [ ] `booking_worker` — availability check, `bookings` insert, calendar event create, confirmation SMS
- [ ] Callback fallback: `tasks.type = 'callback'` when booking fails policy checks

**Exit criteria:** a real inbound "can you come Thursday?" schedules into the owner's Google Calendar without touching a human, or falls back to a callback task with all context.

### Phase 5 — Reactivation + ROI (1 week)

Goal: prove lift.

- [ ] `stale_lead_reactivation` workflow with segment picker
- [ ] ROI dashboard: before/after response time, inquiry→booking, quote turnaround
- [ ] Export daily operator summary via email

**Exit criteria:** business owner sees a number they can show their accountant.

---

## 6. Hard decisions — confirmed

1. **Target path** — `/root/AlchemyAI/RevenueEdge` ✓ (typo in original prompt; `AlchemicalAI` was not intended)
2. **Worker language** — Python ✓
3. **Single webhook service** — ✓ one `re-webhooks` Python/FastAPI service; Retell handles Twilio SMS natively, so one provider surface
4. **Model-router in MVP** — skip ✓; notes captured in [`docs/DEFERRED_MODEL_ROUTER.md`](./docs/DEFERRED_MODEL_ROUTER.md) for drop-in later
5. **OpenClaw integration** — no ✓ (not expected to be needed; the reason for per-tenant containers in Turnkeyclaw was OpenClaw resource isolation, which is moot without OpenClaw)
6. **Per-business container isolation** — no ✓; single multi-tenant stack with RLS
7. **New Supabase project** — ✓; apply `supabase/schema.sql` + `supabase/seed_mvp_defaults.sql` to a fresh project

---

## 7. What this plan assumes

- We are optimizing for **first paying business onboarded in 4–6 weeks**, not platform completeness.
- The wedge offer is missed-call recovery. Everything else is earned trust.
- Copilot-first, autopilot only where rules are crisp. This aligns exactly with the pack's operating principle.
- The first 10 businesses will be onboarded by a human, not self-serve. Self-serve onboarding is a Phase 6+ problem.
- We accept one-tenant-per-Supabase-schema isolation constraints can be added later if required.

---

## 8. Open questions I am not trying to answer here

- Pricing & packaging
- Sales motion beyond "fix one leak for free"
- Compliance posture per vertical (HIPAA for medical, TCPA for SMS at scale, etc.)
- BYOK LLM keys per business
- Multi-location businesses (the `service_area` JSONB column supports this, we just don't exercise it in MVP)
- Voice-AI (full Retell agent conversations vs. SMS-first) — MVP is SMS-first because it's lower risk and lower cost per interaction. Voice agents arrive in Phase 4 or 5 once the text flow is stable.

---

## 9. Phase 0 — executed

Scaffolded on disk. The repo now contains:

- `apps/api/` — FastAPI with health/ready, trace-ID middleware, structured JSON logging, PII redaction, Sentry, error hierarchy, and an internal `/internal/queue/enqueue` endpoint that wraps the `enqueue_job` RPC. Auth ported from SMB-MetaPattern with `tenant_*` → `business_*` renames. SMS + email provider modules ported verbatim.
- `apps/webhooks/` — FastAPI `/webhooks/retell` endpoint with `retell.lib.verify` signature verification, canonicalization of Retell events → `call.missed | call.ended | call.started | message.received`, enqueue-via-`re-api` with idempotency key `inbound:retell:<call_id>:<canonical>`. Small surface area by design (one provider, one endpoint).
- `apps/workers/` — single Python process spawning N asyncio tasks, one per enabled worker. `BaseWorker` claims from `claim_queue_jobs`, handles, then calls `complete_queue_job` / `fail_queue_job` with exponential-backoff-with-jitter. `InboundNormalizerWorker` has Phase-0 logic (record event, hand off to `conversation-intelligence`). Stubs for the four other MVP workers wired in the registry.
- `supabase/schema.sql` and `seed_mvp_defaults.sql` — copied from the pack verbatim.
- `workflows/queue_workflow_pack.yaml` + `QUEUE_WORKFLOWS.md` — copied.
- `docs/DEFERRED_MODEL_ROUTER.md` — file-by-file port plan for the model-router.
- `scripts/bootstrap.sh` — creates `.venv-re`, installs all three service deps, optionally applies the schema via `SUPABASE_DB_URL` + `psql`.
- `scripts/smoke_phase0.sh` — enqueues a dummy job via RPC, claims it, completes it, asserts the row state changed. Proves schema + RPCs are wired before any worker code runs.
- `scripts/run_{api,webhooks,worker}.sh` — dev runners.
- `docker-compose.yml` — three services (`re-api`, `re-webhooks`, `re-workers`) with per-service Dockerfiles.
- `.env.example` — trimmed to MVP surface (Supabase, Retell, Twilio fallback, SendGrid, OpenAI, internal service key, Sentry, worker tunables).

### How to bring Phase 0 online

```bash
cd /root/AlchemyAI/RevenueEdge
cp .env.example .env
# Create a NEW Supabase project. Fill SUPABASE_URL, SUPABASE_SERVICE_KEY,
# SUPABASE_JWT_SECRET. For CLI migrations also set SUPABASE_DB_URL.
./scripts/bootstrap.sh       # installs deps; applies schema if SUPABASE_DB_URL set
./scripts/smoke_phase0.sh    # end-to-end: enqueue + claim + complete via RPCs
# then:
./scripts/run_api.sh         # :8080
./scripts/run_webhooks.sh    # :8081
./scripts/run_worker.sh      # consumes inbound-events and downstream queues
```

### Where Phase 1 picks up

See [`PHASE_1_CHECKLIST.md`](./PHASE_1_CHECKLIST.md). Concretely:

1. Extend `InboundNormalizerWorker.handle` to upsert `contacts`, create `conversations` + `messages` rows, and branch on `event_type`.
2. Replace `ConversationIntelligenceWorker.handle` no-op with an LLM call (OpenAI direct) and decision routing.
3. Replace `OutboundActionWorker.handle` no-op with the ported `providers/sms.py` call + template rendering.
4. Replace `HandoffWorker.handle` no-op with a `tasks` row insert and optional SendGrid operator email.
5. Add the first real CRUD endpoints under `apps/api/app/routes/`: `/v1/businesses`, `/v1/channels`, `/v1/conversations`, `/v1/tasks`.

The scaffolding is deliberately thin. Nothing ported from SMB-MetaPattern has been left as a hidden dependency; anything not yet needed is either skipped or called out in [`REUSE_MAP.md`](./REUSE_MAP.md).

---

## 10. Phase 1 — executed (backend slice)

Phase 1 backend pipeline landed end-to-end on disk. `PHASE_1_CHECKLIST.md`
tracks per-item status; the summary:

**New shared worker library** — `apps/workers/src/lib/`
- `phone.py` — permissive-in / E.164-out normalization matching the
  `contacts.phone_e164` regex constraint.
- `channels.py` — DID → channel/business resolution (with a
  `channel_type_hint` tie-breaker when voice + SMS share the same DID).
- `contacts.py` — lookup-then-insert upsert against the unique partial
  indexes on `contacts(business_id, phone_e164|email)`.
- `conversations.py` — open-conversation reuse policy (single open thread
  per `business_id, contact_id, channel_type`), message insert, context
  loader for the intelligence worker, patch-merged conversation updates.
- `templates.py` — Liquid-ish template loader + renderer (supports the
  seeded `{{contact.first_name | default: "there"}}` subset).
- `hours.py` — business-hours check (weekly JSON schedule, holiday list,
  per-business tz). Not yet enforced but plumbed through the context.
- `llm.py` — OpenAI chat completions in JSON mode with a deterministic
  heuristic fallback when `OPENAI_API_KEY` is empty, so the smoke test
  works without external spend.

**Workers** — `apps/workers/src/workers/`
- `inbound_normalizer.py` — resolves business, upserts contact, opens
  conversation, persists messages, fans out to outbound + intelligence,
  writes audit events.
- `conversation_intelligence.py` — calls the LLM, applies a 0.72
  confidence floor for autopilot, routes to outbound / handoff, persists
  the decision onto the conversation.
- `outbound_action.py` — template or body-literal SMS sends via Retell
  (Twilio fallback), opt-out aware, writes outbound messages + events.
- `handoff.py` — creates `tasks`, flips conversation to
  `awaiting_human`, optional operator email via SendGrid.
- `followup_scheduler.py` remains a stub until Phase 4.

**API additions** — `apps/api/app/routes/`
- `businesses.py` (`GET /v1/businesses`, `GET /v1/businesses/:id`).
- `conversations.py` (`GET /v1/conversations`, `GET /v1/conversations/:id`).
- `tasks.py` (`GET /v1/tasks`, `PATCH /v1/tasks/:id`).
- `metrics.py` (`GET /v1/metrics`, `POST /v1/metrics/rollup`).

**Scheduler + metrics** — `apps/api/app/services/`
- `metrics_rollup.py` aggregates events/leads/quotes/bookings/messages
  into `metric_snapshots` (upserted on `(business_id, metric_date)`).
- `scheduler.py` — in-process asyncio loop; wired into `main.py` via
  `startup`/`shutdown` handlers.

**Smoke tooling**
- `scripts/seed_business.py` — idempotent business + phone/SMS channel
  seed, invokes `seed_revenue_edge_mvp_defaults`.
- `scripts/smoke_phase1.sh` — drives the full missed-call happy path
  against a running stack and asserts downstream events/messages/tasks.

**Deferred from Phase 1** (documented on the checklist, moved to Phase 2):
- Next.js dashboard UI (inbox + settings) — backend APIs are ready; UI
  ships once the live Supabase project is provisioned.
- Per-contact/per-business SMS rate limit (STOP handling is already
  honored via `contacts.metadata.sms_opt_out`).
- Quiet-hours enforcement in `outbound_action` (helper exists).
- STOP/START/HELP handling at the inbound SMS layer — Retell handles
  compliance for DIDs it provisions; we revisit if/when we route direct
  Twilio.
- Explicit `leads` lifecycle writes from the worker chain — today the
  `conversations` row + events carry intent/urgency/confidence, which is
  enough for the MVP dashboard. Lead CRUD arrives alongside the quote
  intake flow in Phase 2.
