# Revenue Edge

Multi-tenant, low-friction SMB revenue-capture agent. Answers inbound demand, qualifies, books or quotes, and escalates only when needed.

See the planning docs before touching code:

- [`PLAN.md`](./PLAN.md) — hybrid-fork strategy, target topology, phased roadmap
- [`REUSE_MAP.md`](./REUSE_MAP.md) — file-by-file pilfer/port/reference/skip inventory from SMB-MetaPattern
- [`PHASE_1_CHECKLIST.md`](./PHASE_1_CHECKLIST.md) — concrete steps for the first vertical slice (missed-call recovery)
- [`SKILL.md`](./SKILL.md) — canonical agent operating specification (copied from `revenue-edge-agent-pack/`)
- [`workflows/QUEUE_WORKFLOWS.md`](./workflows/QUEUE_WORKFLOWS.md) — prose overview of queues and workflows
- [`workflows/queue_workflow_pack.yaml`](./workflows/queue_workflow_pack.yaml) — machine-readable queue contracts
- [`docs/DEFERRED_MODEL_ROUTER.md`](./docs/DEFERRED_MODEL_ROUTER.md) — notes + drop-in port plan for the multi-provider router, deferred post-MVP

## Topology (MVP)

```
Retell ──► re-webhooks (FastAPI)  ──► supabase.queue_jobs
                                          │
                                          │  claim_queue_jobs RPC
                                          ▼
                 ┌────────────────── re-workers (Python) ──────────────────┐
                 │  inbound_normalizer                                     │
                 │  conversation_intelligence                              │
                 │  outbound_action                                        │
                 │  handoff                                                │
                 │  followup_scheduler                                     │
                 └──────────┬──────────────┬──────────────┬────────────────┘
                            │              │              │
                       Retell/Twilio   SendGrid       internal
                       (SMS + voice)   (email)        (tasks, events)

                  re-api (FastAPI) ◄──── re-dashboard (Next.js)
                        │
                        └─► Supabase (Postgres + Auth + RLS + pgvector)
```

Everything runs in a single multi-tenant Supabase project. Tenants are `businesses` rows; RLS enforces isolation.

## Services

| Service | Stack | Role |
|---|---|---|
| `re-webhooks` | Python / FastAPI | Signature-verify Retell events (voice + SMS via Retell-baked Twilio), enqueue `inbound-events` jobs, return < 500 ms |
| `re-api` | Python / FastAPI | Authenticated CRUD for businesses, channels, conversations, leads, tasks, knowledge, metrics |
| `re-workers` | Python | Long-running worker processes that consume `queue_jobs` |
| `re-dashboard` | Next.js 14 | Operator UX: inbox, settings, knowledge, ROI |

## Quickstart (Phase 0)

```bash
cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_JWT_SECRET, RETELL_API_KEY
./scripts/bootstrap.sh       # install Python deps + run schema migration (local Supabase CLI or hosted)
./scripts/smoke_phase0.sh    # end-to-end: enqueue a dummy job, start a worker, confirm it claims + completes
```

## Phase status

- [x] **Phase 0** — scaffolding, schema applied, worker picks up jobs via `claim_queue_jobs`
- [ ] **Phase 1** — missed-call recovery vertical slice (see [`PHASE_1_CHECKLIST.md`](./PHASE_1_CHECKLIST.md))
- [ ] Phase 2 — after-hours intake + FAQ-to-conversion
- [ ] Phase 3 — qualification + quote intake
- [ ] Phase 4 — booking / callback
- [ ] Phase 5 — reactivation + ROI dashboard

## Conventions

- Python 3.11+, `ruff` + `black`, `pyproject.toml` per service
- One worker process per queue consumer, horizontal scaling via replicas
- All persistent state in Supabase; no Redis, no RabbitMQ, no Temporal for MVP
- `trace_id` propagates through every webhook → event → job → action
- Idempotency keys follow the `queue_workflow_pack.yaml` patterns

## Not in this MVP (see `PLAN.md`)

- OpenClaw runtime and chat UX
- Per-tenant container isolation
- Multi-provider LLM router (see `docs/DEFERRED_MODEL_ROUTER.md` for when/how to add it back)
- CRM sync (HubSpot, FollowUpBoss)
- Ringless voicemail, outbound voice-first campaigns
- Multi-language
