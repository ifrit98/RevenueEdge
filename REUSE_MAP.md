# Reuse Map — SMB-MetaPattern → Revenue Edge

File-by-file inventory of what to pilfer from `/root/AlchemyAI/SMB-MetaPattern/`.

Legend:

- **PILFER** — copy with minor edits (rename tables, drop RE references)
- **PORT** — take the pattern, rewrite against pack schema / pack workflow contract
- **REFERENCE** — read for patterns only; do not copy
- **SKIP** — do not use

---

## Supabase schema

| Source | Destination | Action | Notes |
|---|---|---|---|
| `supabase/migrations/0001_init.sql` … `0079_*` | — | SKIP | 79 migrations of RE-specific schema. Adopt pack's `supabase/schema.sql` as canonical instead. |
| `supabase/migrations/0005_job_queue.sql` | — | SKIP | Superseded by pack's `queue_jobs`. |
| `supabase/migrations/0006_job_lock_fn.sql` | — | SKIP | Superseded by pack's `claim_queue_jobs` RPC. |
| `supabase/migrations/0051_inbound_receptionist.sql` | `supabase/schema.sql` (already covered) | REFERENCE | Pack's `channels.config` jsonb + `businesses.hours` already cover the same shape. |
| `supabase/migrations/0014_vector_search_functions.sql` | `supabase/migrations/0002_vector_search.sql` | REFERENCE | If pack's HNSW index isn't sufficient, port the `match_*` RPCs. |
| `supabase/config.toml` | `supabase/config.toml` | PILFER | Minor edits for project name. |

---

## API Gateway — infrastructure modules

| Source | Destination | Action | Notes |
|---|---|---|---|
| `apps/api-gateway/app/auth.py` (267 L) | `apps/api/app/auth.py` | PILFER | Rename `tenants`→`businesses`, `tenant_users`→`business_members`. Keep JWT verify, internal-key fallback, 5-min cache. |
| `apps/api-gateway/app/db.py` (227 L) | `apps/api/app/db.py` | PILFER | Unchanged. Supabase client singleton + `async_execute`. |
| `apps/api-gateway/app/config.py` (272 L) | `apps/api/app/config.py` | PILFER | Trim RE env vars; keep SUPABASE_*, RETELL_*, TWILIO_*, SENDGRID_*, OPENAI_*, SENTRY_DSN. |
| `apps/api-gateway/app/audit.py` | `apps/api/app/middleware/audit.py` | PILFER | Audit middleware writes to `public.audit_log` (already in pack schema). |
| `apps/api-gateway/app/rate_limit.py` (467 L) | `apps/api/app/middleware/rate_limit.py` | PILFER | Consider Redis-less variant for MVP. |
| `apps/api-gateway/app/circuit_breaker.py` (259 L) | `apps/api/app/middleware/circuit_breaker.py` | PILFER | Unchanged. |
| `apps/api-gateway/app/error_handling.py` (579 L) | `apps/api/app/error_handling.py` | PILFER | Unchanged. `AppError` hierarchy is solid. |
| `apps/api-gateway/app/monitoring.py` | `apps/api/app/monitoring.py` | PILFER | Sentry init + tenant context (rename tenant→business). |
| `apps/api-gateway/app/prometheus_metrics.py` | `apps/api/app/prometheus_metrics.py` | PILFER | Rename RE-specific metric names (VOICE_CALLS, etc.) to be vertical-neutral. |
| `apps/api-gateway/app/budget.py` (300 L) | — | SKIP (MVP) | Usage budgeting; add in Phase 6. |
| `apps/api-gateway/app/oauth_refresh.py` (266 L) | `apps/api/app/oauth_refresh.py` | PORT (Phase 4) | Google OAuth refresh for calendar sync. Leave HubSpot/CRM refresh out for MVP. |
| `apps/api-gateway/app/scheduler.py` (246 L) | `apps/api/app/scheduler.py` | PORT | Replace heartbeat/memory loops with `metric_snapshots` rollup + `reactivation.batch_requested` trigger. |
| `apps/api-gateway/app/redis_rate_limit.py` | — | SKIP (MVP) | |
| `apps/api-gateway/app/pii_filter.py` | `apps/api/app/pii_filter.py` | PILFER | Useful for logging hygiene. |
| `apps/api-gateway/app/logging_config.py` | `apps/api/app/logging_config.py` | PILFER | Unchanged. |

---

## Providers

| Source | Destination | Action | Notes |
|---|---|---|---|
| `apps/api-gateway/app/providers/sms.py` | `apps/api/app/providers/sms.py` | PILFER | Retell primary + Twilio fallback. Already well-factored. |
| `apps/api-gateway/app/providers/email.py` | `apps/api/app/providers/email.py` | PILFER | SendGrid HTTP API. Add Mailgun adapter later if needed. |

---

## Webhooks

| Source | Destination | Action | Notes |
|---|---|---|---|
| `apps/api-gateway/app/webhooks_retell.py` (1,170 L) | `apps/webhooks/app/retell.py` (~300 L) | PORT | Keep only: signature verification, call.started, call.ended, call_analyzed routing. Drop: Pipeline v2 integration, agent-specific routing (Kelly/Avery/Jordan/Skye), CMA chat injection. New responsibility: enqueue `inbound-events` job per pack spec. |
| `apps/api-gateway/app/retell_inbound.py` (212 L) | fold into `apps/webhooks/app/retell.py` | PORT | Inbound routing: lookup `channels` (not `phone_numbers`), build dynamic vars for Retell agent, enqueue `call.missed` / `message.received` event. |
| `apps/api-gateway/app/retell_events.py` (1,504 L) | — | REFERENCE | Heavy RE integration (Google Calendar fallback, agent state machine). Read for event-shape patterns, do not port. |
| `apps/api-gateway/app/webhooks_twilio.py` (363 L) | `apps/webhooks/app/twilio.py` (~200 L) | PORT | Keep signature verification + STOP/HELP/START handling + `message.received` event emission. Drop tenant-scoped bits that reference `phone_numbers` directly; use `channels`. |
| `apps/api-gateway/app/webhooks_followupboss.py` | — | SKIP (MVP) | CRM sync is post-MVP. |
| `apps/api-gateway/app/webhooks_hubspot.py` (537 L) | — | SKIP (MVP) | Same. |

---

## Intelligence / post-call analysis

| Source | Destination | Action | Notes |
|---|---|---|---|
| `apps/api-gateway/app/services/retell_post_call.py` (1,076 L) | — | REFERENCE | Reference for transcript parsing + `collected_dynamic_variables` handling. The agent-specific `_process_kelly_call`, `_process_avery_call`, etc. are RE-specific. The *structure* transfers to `conversation_intelligence_worker`. |
| `apps/api-gateway/app/services/retell_service.py` (741 L) | `apps/api/app/services/retell_service.py` (~300 L) | PORT | Keep `make_voice_call` and `send_sms_chat` as outbound helpers. Drop the 5-purpose routing (vendor_scheduling, lead_qualification, etc.) — Revenue Edge uses a single intent-driven flow. Keep `_stringify_dynamic_vars` helper. |
| `apps/api-gateway/app/services/pipeline_service.py` (1,718 L) | — | SKIP | RE buyer/seller pipeline state machine. Revenue Edge lead lifecycle is pack-schema enums. |
| `apps/api-gateway/app/services/crm_pipeline_service.py` (618 L) | — | SKIP | Same. |

---

## Knowledge / memory / skills

| Source | Destination | Action | Notes |
|---|---|---|---|
| `apps/api-gateway/app/services/memory_service.py` (2,154 L) | — | SKIP | RE-agent episodic memory. Pack's `knowledge_items` + pgvector is the canonical replacement. |
| `apps/api-gateway/app/services/knowledge_service.py` (39 L) | — | SKIP | Already a deprecated stub. |
| `apps/api-gateway/app/services/skill_service.py` (204 L) | — | SKIP | OpenClaw skill loader. |
| `apps/api-gateway/app/services/soul_service.py` (243 L) | — | SKIP | RE-agent personality config. |
| `scripts/backfill_embeddings.py` | `scripts/backfill_embeddings.py` | PORT | Rewrite against `knowledge_items` rows, embed `title + content`, write to `embedding vector(1536)`. |
| `apps/api-gateway/app/tools/*.py` | — | SKIP | All tool definitions are RE-specific (cma_tools, listings, loans, market). |

---

## Workers

| Source | Destination | Action | Notes |
|---|---|---|---|
| `workers/campaign_worker.ts` (480 L) | `apps/workers/src/index.ts` (if TS) | REFERENCE | Reference for graceful-shutdown, Sentry wiring, internal-gateway header helpers, job-dispatch switch. Rewrite against pack's `claim_queue_jobs` RPC (not `lock_jobs_rpc`). Job shape is pack's payload contract (`event_type` + `trace_id` + `idempotency_key`), not SMB's `kind` + `tenant_id`. |
| `workers/logger.ts` | `apps/workers/src/lib/logger.ts` | PILFER | Pino logger, unchanged. |
| `workers/Dockerfile` | `apps/workers/Dockerfile` | PILFER | Unchanged. |
| `workers/package.json` | `apps/workers/package.json` | PILFER | Same deps (supabase-js, retell-sdk, pino, sentry). |

---

## Dashboard

| Source | Destination | Action | Notes |
|---|---|---|---|
| `apps/dashboard/middleware.ts` | `apps/dashboard/middleware.ts` | PILFER | Supabase auth middleware. |
| `apps/dashboard/lib/` (Supabase client, auth helpers, types) | `apps/dashboard/lib/` | PILFER | Keep. |
| `apps/dashboard/app/(auth)/` | `apps/dashboard/app/(auth)/` | PILFER | Login/signup/reset. |
| `apps/dashboard/app/(admin)/reception/` | `apps/dashboard/app/(app)/inbox/` | PORT | Rename + restructure for `tasks` + `conversations` pack schema. |
| `apps/dashboard/app/(admin)/tasks/` | `apps/dashboard/app/(app)/inbox/handoffs/` | PORT | Already task-list oriented. |
| `apps/dashboard/app/(admin)/contacts/` | `apps/dashboard/app/(app)/contacts/` | PORT | Simplify columns (drop pipeline_stage RE values). |
| `apps/dashboard/app/(admin)/metrics/` | `apps/dashboard/app/(app)/dashboard/` | PORT | Reshape around pack's `metric_snapshots`. |
| `apps/dashboard/app/(admin)/settings/` | `apps/dashboard/app/(app)/settings/` | PORT | Adapt to `businesses` + `channels` + `business_rules`. |
| `apps/dashboard/app/(admin)/knowledge/` | `apps/dashboard/app/(app)/knowledge/` | PORT | Rewrite against `knowledge_sources` + `knowledge_items` + `approved` toggle. |
| `apps/dashboard/app/(admin)/cma/` | — | SKIP | |
| `apps/dashboard/app/(admin)/listings/` | — | SKIP | |
| `apps/dashboard/app/(admin)/docs/` | — | SKIP (MVP) | Document handling not in MVP. |
| `apps/dashboard/app/(admin)/onboarding/` | `apps/dashboard/app/(app)/onboarding/` | PORT | Strip RE-specific questions; use SKILL.md onboarding checklist. |
| `apps/dashboard/app/(admin)/campaigns/` | — | SKIP (MVP) | Revisit as a "reactivation campaigns" feature post-MVP. |
| `apps/dashboard/app/(admin)/vendors/` | — | SKIP | |
| `apps/dashboard/app/(admin)/integrations/` | `apps/dashboard/app/(app)/settings/integrations/` | PORT | Keep Google/OAuth connect pattern; drop HubSpot/FollowUpBoss for MVP. |
| `apps/dashboard/app/api/chat/` | — | SKIP (MVP) | OpenClaw chat integration. |

---

## Infra

| Source | Destination | Action | Notes |
|---|---|---|---|
| `docker-compose.yml` (root) | `docker-compose.yml` | PORT | Trim to 5 services: re-api, re-webhooks, re-dashboard, re-workers, re-rollup. Drop gateway, tenant-api, webhook-receiver. |
| `infra/caddy/Caddyfile` | `infra/caddy/Caddyfile` | PORT | Strip per-tenant route markers; single-business routing. |
| `infra/prometheus/` | `infra/prometheus/` | PILFER (optional) | Can ship MVP without. |
| `infra/grafana/` | — | SKIP (MVP) | Rely on Sentry + `/metrics` endpoint. |
| `scripts/deploy-hetzner.sh` | `scripts/deploy-hetzner.sh` | PILFER | Useful as a one-liner deploy script. |
| `scripts/seed_demo_data.py` | `scripts/seed_business.py` | PORT | Rewrite for pack schema. |
| `scripts/smoke_test.sh` | `scripts/smoke_test.sh` | PORT | Structure transfers. |
| `.env.example` | `.env.example` | PORT | Trim RE-specific vars. |
| `apps/model-router/` (whole subtree) | `apps/router/` | PILFER (optional) | Defer to post-MVP unless multi-provider needed. |
| `apps/api-gateway/Dockerfile` | `apps/api/Dockerfile` | PORT | Simpler deps list. |

---

## Legacy / skip list (explicit)

These never leave SMB-MetaPattern:

- `apps/api-gateway/app/agents/` — RE agent personas
- `apps/api-gateway/app/crew/` — CrewAI integration
- `apps/api-gateway/app/services/cma_*.py` — CMA report generation
- `apps/api-gateway/app/services/investor_engine.py`
- `apps/api-gateway/app/services/listing_service.py`, `loan_service.py`, `market_data_service.py`, `rentcast_provider.py`
- `apps/api-gateway/app/services/morning_briefing.py`, `heartbeat_service.py`
- `apps/api-gateway/app/services/phone_provisioning.py` — tenant-scoped phone inventory
- `apps/api-gateway/app/services/vendor_service.py`, `client_care_service.py`, `compliance_sentinel.py`
- `apps/api-gateway/app/workflows/` — RE-specific workflow triggers
- `apps/api-gateway/app/api/cma.py`, `listings.py`, `intelligence.py`, `agents.py`, `briefing.py`, `tools_invoke.py`
- `apps/api-gateway/app/legacy_dispatch.py` — legacy orchestrator dispatch
- `turnkeyclaw/` — OpenClaw gateway + skills (entire subtree)
- `openclaw-re/` — OpenClaw fork
- `packages/vertical-packs/`, `packages/forms/`, `packages/templates/` — RE-specific packs (reference only)
- `scripts/ingest_rate_data.py`, `ingest_redfin_data.py` — RE data ingestion
- `scripts/provision-tenant-stack.sh` — tenant-api driver
- `tests/` under RE-agent subtrees

---

## Summary counts

- **PILFER** (minor edits): ~12 Python modules, ~6 TS modules, Docker/Caddy config
- **PORT** (structural rewrite): ~10 Python modules, ~5 Next.js pages, 1 worker loop
- **REFERENCE**: 3 large Python services (retell_post_call, retell_events, pipeline_service) + campaign_worker.ts
- **SKIP**: ~50 files totalling ~25,000 lines of RE-specific code
- **Estimated transplantable code**: ~4,000–6,000 lines after porting. The rest is new code (workers + new FastAPI endpoints + new dashboard pages).
