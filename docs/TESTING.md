# Testing Guide

## Philosophy

Revenue Edge uses a layered testing approach:

1. **Smoke tests** -- End-to-end scripts that seed data, send webhooks, and assert database state. These validate that the full pipeline works across services.
2. **CI compile/build checks** -- Automated verification that all Python modules import cleanly and the dashboard builds without errors.
3. **Docker build validation** -- CI builds all container images to catch Dockerfile and dependency issues.

There is no dedicated unit test suite yet. The smoke scripts serve as the primary quality gate during development.

## Smoke Tests

All smoke scripts live in `scripts/` and require:
- A configured `.env` with valid Supabase credentials
- The API service running (some tests also need workers running)
- The Python virtual environment from `bootstrap.sh`

### Inventory

| Script | Phase | What It Validates |
|--------|-------|-------------------|
| `smoke_phase0.sh` | 0 - Queue | Enqueues an `inbound-events` job via RPC, claims it, completes it, and asserts the job reaches `succeeded` status. Validates the core queue mechanics. |
| `smoke_phase1.sh` | 1 - Missed Call | Seeds a business, POSTs a Retell `call_ended` webhook, polls for an `outbound.sms.sent` event and a `conversation.classified` event. Validates the full inbound -> normalize -> classify -> respond pipeline. |
| `smoke_phase2.sh` | 2 - After-Hours/FAQ | Seeds a business with business hours and knowledge items, sends after-hours and unanswerable SMS webhooks, asserts appropriate replies, classification, and `knowledge_gap` task creation. |
| `smoke_phase3.sh` | 3 - Quote Flow | Seeds a business with a service, simulates a quote request conversation via webhooks, polls for a quote in `awaiting_review` status and a `quote_review` task, approves the quote via the API, and asserts a `quote.sent` event + follow-up scheduling. |
| `smoke_phase4.sh` | 4 - Booking | Seeds a business with booking enabled, sends a booking request webhook, polls for either a created booking or a `callback` task (when no Google Calendar is connected), and verifies customer notification. |
| `smoke_phase5.sh` | 5 - Reactivation/ROI | Seeds stale leads, calls the reactivation preview and launch endpoints, verifies batch status, triggers a metrics rollup, and checks the metrics comparison endpoint. |

### Running

```bash
# Run all phases sequentially
for i in 0 1 2 3 4 5; do
  echo "=== Phase $i ==="
  ./scripts/smoke_phase${i}.sh
done

# Run a single phase
./scripts/smoke_phase3.sh
```

### Seed Script

`scripts/seed_business.py` creates a test business with channels and optional service presets:

```bash
source .venv-re/bin/activate
python scripts/seed_business.py \
  --name "Test Business" \
  --vertical home_services \
  --services plumbing
```

This is called internally by the smoke scripts but can also be used standalone for manual testing.

## CI Pipeline

The GitHub Actions workflow at `.github/workflows/ci.yml` runs on push and PR to `main`.

### Jobs

| Job | What It Checks |
|-----|----------------|
| **python-check** | For each Python service (`api`, `webhooks`, `workers`): installs dependencies, compiles `main.py`, and walks all subpackages to verify imports resolve. Catches missing dependencies and syntax errors. |
| **dashboard-build** | Installs npm dependencies, runs `tsc --noEmit` for type checking, and runs `npm run build` to verify the Next.js production build succeeds. Uses placeholder env vars for build-time variables. |
| **docker-build** | Builds Docker images for `api`, `webhooks`, and `workers` to catch Dockerfile issues and dependency resolution problems. Depends on `python-check` and `dashboard-build` passing. |
| **shellcheck** | Runs ShellCheck on all `scripts/*.sh` files. Currently non-blocking (`|| true`) to avoid failing on minor style issues. |

### Concurrency

CI uses `concurrency: ci-${{ github.ref }}` with `cancel-in-progress: true`, so pushing a new commit cancels any running CI for the same branch.

## Future Testing

### Unit Tests

When adding unit tests:
- Python tests should go in `apps/<service>/tests/` using `pytest`
- Dashboard tests should go in `apps/dashboard/__tests__/` or colocated `*.test.tsx` files
- Add a `test` job to `ci.yml` that runs after the compile checks

### Integration Tests

True end-to-end integration tests that spin up all services in Docker:
- Use Docker Compose to start the full stack
- Run the smoke scripts against the containerized services
- This validates container networking, health checks, and the full request path

### Load Testing

For queue throughput and API performance testing:
- Use `locust` or `k6` against the API endpoints
- Monitor `queue_jobs` claim latency and worker processing times
- The `metric_snapshots` table provides daily aggregate performance data
