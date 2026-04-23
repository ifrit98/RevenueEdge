# Production Deployment Guide

## Architecture Overview

Production runs five containers behind a Caddy reverse proxy, all connected to a hosted Supabase project:

```
Internet
   │
   ▼
Caddy (:80/:443, automatic HTTPS)
   ├── /webhooks/*   → re-webhooks:8081
   ├── /v1/*         → re-api:8080
   ├── /health       → re-api:8080
   ├── /internal/*   → 403 Forbidden
   └── /*            → re-dashboard:3000
```

## Docker Compose Production

The production configuration lives in `docker-compose.prod.yml`. Start with:

```bash
export DOMAIN=yourdomain.com
export ACME_EMAIL=admin@yourdomain.com
export TAG=latest  # or a specific image tag
# Export all required env vars (see below)

docker-compose -f docker-compose.prod.yml up -d
```

### Services

| Service | Image | Ports | Health Check |
|---------|-------|-------|-------------|
| `caddy` | `caddy:2-alpine` | 80, 443 (public) | -- |
| `re-api` | `revenueedge/api:$TAG` | 8080 (internal) | `GET /health` every 15s |
| `re-webhooks` | `revenueedge/webhooks:$TAG` | 8081 (internal) | `GET /health` every 15s |
| `re-workers` | `revenueedge/workers:$TAG` | -- | -- |
| `re-dashboard` | `revenueedge/dashboard:$TAG` | 3000 (internal) | `wget --spider` every 15s |

### Resource Limits

| Service | Memory | CPUs |
|---------|--------|------|
| caddy | 128 MB | 0.25 |
| re-api | 512 MB | 1.0 |
| re-webhooks | 256 MB | 0.5 |
| re-workers | 512 MB | 1.0 |
| re-dashboard | 512 MB | 1.0 |

## Caddy Configuration

The Caddyfile at `infra/caddy/Caddyfile` handles:

- **Automatic HTTPS** via Let's Encrypt (set `DOMAIN` and `ACME_EMAIL` env vars)
- **Path routing** to backend services
- **Internal endpoint blocking** (`/internal/*` returns 403)
- **JSON access logging** to stdout

For custom domains, set the `DOMAIN` environment variable. Caddy automatically provisions and renews TLS certificates.

## Required Environment Variables

Production does not use `.env` files. All secrets are passed as environment variables (via your orchestrator, secrets manager, or shell exports).

### All Services

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes | Service role key (bypasses RLS) |
| `SUPABASE_JWT_SECRET` | Yes | JWT verification secret |
| `ENVIRONMENT` | Yes | Set to `production` |
| `LOG_LEVEL` | No | Default: `info` |

### re-webhooks (additional)

| Variable | Required | Description |
|----------|----------|-------------|
| `RETELL_API_KEY` | Yes | Webhook signature verification |
| `INTERNAL_SERVICE_KEY` | Yes | Auth for API calls |

### re-workers (additional)

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | LLM classification and embeddings |
| `RETELL_API_KEY` | Yes | SMS sending |
| `INTERNAL_SERVICE_KEY` | Yes | Auth for API calls |
| `WORKERS` | Yes | Comma-separated worker list |
| `TWILIO_ACCOUNT_SID` | No | SMS fallback |
| `TWILIO_AUTH_TOKEN` | No | SMS fallback |
| `SENDGRID_API_KEY` | No | Email sending |
| `GOOGLE_CLIENT_ID` | No | Calendar integration |
| `GOOGLE_CLIENT_SECRET` | No | Calendar integration |
| `SENTRY_DSN` | No | Error monitoring |

### re-dashboard (build args)

These are set at build time, not runtime:

| Build Arg | Description |
|-----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase URL for client-side auth |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Anon key for client-side auth |
| `NEXT_PUBLIC_API_URL` | Public URL of the API (e.g., `https://api.yourdomain.com`) |

### Caddy

| Variable | Default | Description |
|----------|---------|-------------|
| `DOMAIN` | `revenueedge.localhost` | Site domain for TLS |
| `ACME_EMAIL` | `admin@revenueedge.ai` | Email for Let's Encrypt |

See [`ENV_VARS.md`](./ENV_VARS.md) for the complete reference.

## Database Migrations

### Initial Setup

Apply the full schema to a new Supabase project:

```bash
psql "$SUPABASE_DB_URL" -f supabase/schema.sql
psql "$SUPABASE_DB_URL" -f supabase/seed_mvp_defaults.sql
```

### Incremental Migrations

Apply in order after the initial schema:

```bash
psql "$SUPABASE_DB_URL" -f supabase/migrations/0002_match_knowledge_rpc.sql
psql "$SUPABASE_DB_URL" -f supabase/migrations/0003_upload_tokens.sql
psql "$SUPABASE_DB_URL" -f supabase/migrations/0004_hardening.sql
```

Migrations are idempotent (use `CREATE OR REPLACE`, `ON CONFLICT DO NOTHING`, and `DROP IF EXISTS`).

### Schema vs Migrations

- `supabase/schema.sql` is the full bootstrap file. Apply it once to a new project.
- `supabase/migrations/` contains incremental patches. Apply them in numeric order after the initial schema.
- If you need to rebuild from scratch, apply `schema.sql` first, then all migrations in order.

## Scaling Workers

The `WORKERS` environment variable controls which queue consumers run in a given container. Scale horizontally by running multiple containers with different worker subsets:

```yaml
# High-throughput inbound processing
re-workers-inbound:
  environment:
    WORKERS: inbound_normalizer,conversation_intelligence

# Outbound and scheduling
re-workers-outbound:
  environment:
    WORKERS: outbound_action,followup_scheduler,handoff

# Background processing
re-workers-background:
  environment:
    WORKERS: knowledge_ingestion,quote_drafting,booking
```

Each worker type can safely run in multiple replicas -- the `claim_queue_jobs` RPC uses `FOR UPDATE SKIP LOCKED` to prevent double-processing.

## Health Checks and Monitoring

### Endpoints

- `GET /health` -- Liveness: returns 200 if the process is running
- `GET /ready` -- Readiness: returns 200 if Supabase is reachable

### Sentry

Set `SENTRY_DSN` on `re-api` and `re-workers` to enable error monitoring. Configure sample rates with `SENTRY_SAMPLE_RATE` and `SENTRY_TRACES_SAMPLE_RATE`.

### Logging

All services emit structured JSON logs. In production Docker Compose, logs are capped at 10 MB per file with 3 rotated files per service. Use `docker-compose logs -f <service>` or ship to your log aggregator.

### Stuck Job Recovery

The worker reaper automatically resets jobs stuck in `running` state for more than 10 minutes. This handles worker crashes, OOM kills, and network partitions without manual intervention.
