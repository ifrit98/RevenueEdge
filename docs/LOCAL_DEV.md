# Local Development Guide

## Prerequisites

- **Python 3.11+** (3.12 recommended)
- **Node.js 20+** and npm (for the dashboard)
- **Docker** and Docker Compose (optional, for containerized dev)
- **psql** CLI (optional, for direct database access)
- A **Supabase project** (hosted or local via Supabase CLI)

## Initial Setup

### 1. Clone and Configure

```bash
git clone <repo-url> && cd RevenueEdge
cp .env.example .env
```

Edit `.env` and fill in the required values. At minimum you need:

| Variable | Where to Get It |
|----------|----------------|
| `SUPABASE_URL` | Supabase project settings > API |
| `SUPABASE_ANON_KEY` | Supabase project settings > API |
| `SUPABASE_SERVICE_KEY` | Supabase project settings > API |
| `SUPABASE_JWT_SECRET` | Supabase project settings > API > JWT Secret |
| `INTERNAL_SERVICE_KEY` | Generate any strong random string |

Optional but recommended:
- `RETELL_API_KEY` -- Required for webhook signature verification and SMS
- `OPENAI_API_KEY` -- Required for LLM classification (falls back to heuristic without it)

See [`ENV_VARS.md`](./ENV_VARS.md) for the full reference.

### 2. Bootstrap

```bash
./scripts/bootstrap.sh
```

This script:
1. Creates a Python virtual environment at `.venv-re`
2. Installs dependencies for `apps/api`, `apps/webhooks`, and `apps/workers`
3. If `SUPABASE_DB_URL` is set in `.env`, applies `supabase/schema.sql` and `supabase/seed_mvp_defaults.sql` via `psql`

If you don't have `SUPABASE_DB_URL`, apply the schema manually through the Supabase SQL Editor.

### 3. Apply Migrations

After the initial schema, apply incremental migrations:

```bash
export SUPABASE_DB_URL="postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres"
psql "$SUPABASE_DB_URL" -f supabase/migrations/0002_match_knowledge_rpc.sql
psql "$SUPABASE_DB_URL" -f supabase/migrations/0003_upload_tokens.sql
psql "$SUPABASE_DB_URL" -f supabase/migrations/0004_hardening.sql
```

### 4. Dashboard Setup

```bash
cd apps/dashboard
npm install
```

Create `apps/dashboard/.env.local`:

```
NEXT_PUBLIC_SUPABASE_URL=<your supabase URL>
NEXT_PUBLIC_SUPABASE_ANON_KEY=<your anon key>
NEXT_PUBLIC_API_URL=http://localhost:8080
```

## Running Services

### Option A: Individual Scripts (Recommended for Development)

Each script sources `.env` and activates the virtual environment automatically.

**Terminal 1 -- API:**
```bash
./scripts/run_api.sh
# Runs on http://localhost:8080 with hot reload
```

**Terminal 2 -- Webhooks:**
```bash
./scripts/run_webhooks.sh
# Runs on http://localhost:8081 with hot reload
```

**Terminal 3 -- Workers:**
```bash
./scripts/run_worker.sh
# Runs all workers defined in WORKERS env var
# Override: WORKERS=inbound_normalizer,conversation_intelligence ./scripts/run_worker.sh
```

**Terminal 4 -- Dashboard:**
```bash
cd apps/dashboard && npm run dev
# Runs on http://localhost:3000
```

### Option B: Docker Compose

```bash
docker-compose up --build
```

This starts `re-api`, `re-webhooks`, and `re-workers`. The dashboard is not included in the dev compose file; run it separately with `npm run dev`.

## Seeding Test Data

Create a test business with channels:

```bash
source .venv-re/bin/activate
python scripts/seed_business.py --name "Test Plumbing Co" --vertical home_services --services plumbing
```

This creates:
- A business row with the given name and vertical
- Phone and SMS channels configured for Retell
- Default message templates, business rules, and automation workflows (via `seed_revenue_edge_mvp_defaults`)
- Vertical-specific service presets (when `--services` is provided)

The script prints a JSON summary with the `business_id` and channel IDs.

## Smoke Tests

Smoke tests validate end-to-end flows by seeding data, sending webhooks, and asserting database state.

```bash
./scripts/smoke_phase0.sh    # Queue mechanics: enqueue -> claim -> complete
./scripts/smoke_phase1.sh    # Missed-call recovery: webhook -> SMS reply
./scripts/smoke_phase2.sh    # After-hours + FAQ: hours check, knowledge search
./scripts/smoke_phase3.sh    # Quote flow: intake -> draft -> approve -> send
./scripts/smoke_phase4.sh    # Booking: request -> GCal or callback fallback
./scripts/smoke_phase5.sh    # Reactivation: segment -> launch -> metrics
```

All smoke scripts require:
- `.env` configured with valid Supabase credentials
- The API and workers running (or at least the API for webhook-based tests)
- The virtual environment created by `bootstrap.sh`

## Common Troubleshooting

### "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set"

The `.env` file is missing or the variables are empty. Ensure `.env` exists in the repo root with valid values.

### Workers claim no jobs

- Check that the API is running (workers enqueue through it)
- Verify `WORKERS` env var includes the worker you expect
- Check `queue_jobs` table for stuck `running` jobs (the reaper handles these automatically after 10 minutes)

### Dashboard shows "Unauthorized"

- Ensure `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` in `apps/dashboard/.env.local` match your Supabase project
- Create a user via the Supabase Auth dashboard or signup page
- The user must be added to `business_members` for a business to see any data

### LLM classification returns heuristic fallback

This is expected when `OPENAI_API_KEY` is not set. The heuristic classifier provides basic intent detection for development. Set the key for full LLM-powered classification.

### Google Calendar integration not working

- Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`
- Connect via the dashboard Settings > Integrations page
- The OAuth callback URL must be accessible (use a tunnel like ngrok for local dev)
