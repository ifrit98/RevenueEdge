# Revenue Edge

Multi-tenant, low-friction SMB revenue-capture agent. Answers inbound demand, qualifies, books or quotes, and escalates only when needed.

## Documentation

| Document | Description |
|----------|-------------|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | System design, data flow, multi-tenancy, queue lifecycle, auth model |
| [`PLAN.md`](./PLAN.md) | Hybrid-fork strategy, target topology, phased roadmap |
| [`SKILL.md`](./SKILL.md) | Canonical agent operating specification |
| [`CONTRIBUTING.md`](./CONTRIBUTING.md) | Code style, PR workflow, commit conventions |
| [`docs/LOCAL_DEV.md`](./docs/LOCAL_DEV.md) | Local development setup guide |
| [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md) | Production deployment with Docker Compose + Caddy |
| [`docs/API_REFERENCE.md`](./docs/API_REFERENCE.md) | Complete REST API endpoint inventory |
| [`docs/DATABASE.md`](./docs/DATABASE.md) | Schema guide, ERD, RPCs, migration strategy |
| [`docs/ENV_VARS.md`](./docs/ENV_VARS.md) | Environment variable reference |
| [`docs/TESTING.md`](./docs/TESTING.md) | Smoke tests, CI pipeline, testing strategy |
| [`docs/DASHBOARD_SPEC.md`](./docs/DASHBOARD_SPEC.md) | Dashboard UI specification |
| [`docs/DEFERRED_TASKS.md`](./docs/DEFERRED_TASKS.md) | Inventory of deferred and completed tasks |
| [`docs/DEFERRED_MODEL_ROUTER.md`](./docs/DEFERRED_MODEL_ROUTER.md) | Multi-provider LLM router upgrade plan |
| [`docs/DEFERRED_INFRASTRUCTURE.md`](./docs/DEFERRED_INFRASTRUCTURE.md) | Deferred infrastructure items |
| [`workflows/QUEUE_WORKFLOWS.md`](./workflows/QUEUE_WORKFLOWS.md) | Queue runbook and workflow narratives |
| [`REUSE_MAP.md`](./REUSE_MAP.md) | File-by-file pilfer/port/skip inventory from SMB-MetaPattern |

Phase checklists: [Phase 1](./PHASE_1_CHECKLIST.md) | [Phase 2](./PHASE_2_CHECKLIST.md) | [Phase 3](./PHASE_3_CHECKLIST.md) | [Phase 4](./PHASE_4_CHECKLIST.md) | [Phase 5](./PHASE_5_CHECKLIST.md)

## Topology

```
                              ┌──────────────────────────────────────────────────┐
                              │             Supabase (hosted)                    │
  Retell ──► re-webhooks ─────┤  Postgres + Auth + RLS + pgvector + pg_trgm     │
             (FastAPI:8081)   │  queue_jobs table (claim/complete/fail RPCs)     │
                              │  Storage (photos bucket)                        │
                              └────────────┬─────────────────────┬──────────────┘
                                           │ claim_queue_jobs    │
                                           ▼                     │
              ┌──────────── re-workers (Python) ─────────────┐   │
              │  inbound_normalizer                          │   │
              │  conversation_intelligence  (LLM classify)   │   │
              │  outbound_action            (SMS/email send)  │   │
              │  handoff                    (escalation)      │   │
              │  followup_scheduler         (drip sequences)  │   │
              │  knowledge_ingestion        (embed/scrape)    │   │
              │  quote_drafting             (LLM draft)       │   │
              │  booking                    (GCal sync)       │   │
              │  reaper                     (stale job reset)  │   │
              └──────┬──────────┬──────────┬─────────────────┘   │
                     │          │          │                      │
                Retell/Twilio  SendGrid  Google Calendar          │
                (SMS + voice)  (email)   (booking)               │
                                                                  │
  Operator ──► re-dashboard (Next.js 16) ──► re-api (FastAPI) ───┘
               :3000                         :8080
```

Everything runs in a single multi-tenant Supabase project. Tenants are `businesses` rows; RLS enforces isolation.

## Services

| Service | Stack | Role |
|---------|-------|------|
| `re-webhooks` | Python / FastAPI | Signature-verify Retell events (voice + SMS), enqueue `inbound-events` jobs, return < 500 ms |
| `re-api` | Python / FastAPI | Authenticated CRUD for businesses, channels, conversations, leads, tasks, knowledge, metrics, quotes, bookings, uploads, integrations; internal queue enqueue endpoint; daily rollup scheduler |
| `re-workers` | Python | 8 queue consumers + reaper: inbound normalizer, conversation intelligence, outbound action, handoff, followup scheduler, knowledge ingestion, quote drafting, booking |
| `re-dashboard` | Next.js 16 / React 19 | Operator UX: inbox, leads, quotes, bookings, knowledge, reactivation, settings, ROI metrics |

## Quickstart

```bash
cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_JWT_SECRET, RETELL_API_KEY
./scripts/bootstrap.sh       # install Python deps + apply schema (if SUPABASE_DB_URL set)
./scripts/smoke_phase0.sh    # end-to-end: enqueue a job, start a worker, confirm it completes

# Dashboard (separate terminal)
cd apps/dashboard && npm install && npm run dev
```

See [`docs/LOCAL_DEV.md`](./docs/LOCAL_DEV.md) for the full setup guide.

## Phase Status

- [x] **Phase 0** — Scaffolding, schema applied, worker picks up jobs via `claim_queue_jobs`
- [x] **Phase 1** — Missed-call recovery vertical slice
- [x] **Phase 2** — After-hours intake + FAQ-to-conversion
- [x] **Phase 3** — Qualification + quote intake
- [x] **Phase 4** — Booking / callback with Google Calendar
- [x] **Phase 5** — Reactivation + ROI dashboard
- [x] **Dashboard** — Full Next.js operator UI (12 pages)
- [x] **Hardening** — External service audit + 17 resilience fixes

## Conventions

- Python 3.11+, `ruff` + `black`, `pyproject.toml` per service
- One worker process per queue consumer, horizontal scaling via replicas
- All persistent state in Supabase; no Redis, no RabbitMQ, no Temporal for MVP
- `trace_id` propagates through every webhook -> event -> job -> action
- Idempotency keys follow the `workflows/queue_workflow_pack.yaml` patterns
- Structured JSON logging with `X-Trace-ID` header propagation

## Not in This MVP

See [`PLAN.md`](./PLAN.md) and [`docs/DEFERRED_INFRASTRUCTURE.md`](./docs/DEFERRED_INFRASTRUCTURE.md) for details.

- OpenClaw runtime and chat UX
- Per-tenant container isolation
- Multi-provider LLM router (see [`docs/DEFERRED_MODEL_ROUTER.md`](./docs/DEFERRED_MODEL_ROUTER.md))
- CRM sync (HubSpot, FollowUpBoss)
- Ringless voicemail, outbound voice-first campaigns
- Multi-language support
