# Contributing

## Code Style

### Python (api, webhooks, workers)

- Python 3.11+ with type hints
- Format with `black` (default settings)
- Lint with `ruff`
- Module-level docstrings on every file explaining its purpose
- Avoid inline comments that merely narrate what the code does; comment only non-obvious intent or trade-offs

### TypeScript (dashboard)

- Next.js 16 / React 19 conventions
- Tailwind CSS for styling
- TypeScript strict mode (`tsc --noEmit` must pass)
- Components in `src/components/`, pages under `src/app/`

## Project Structure

### Adding a New Worker

1. Create `apps/workers/src/workers/your_worker.py` with a class extending `BaseWorker`
2. Set `queue_name` to match a queue defined in `workflows/queue_workflow_pack.yaml`
3. Implement `async handle(self, job: Job) -> Optional[dict]`
4. Register the worker in `apps/workers/src/main.py` `_REGISTRY`
5. Add it to the `WORKERS` env var in Docker Compose files
6. Document the queue contract in `workflows/QUEUE_WORKFLOWS.md`

### Adding a New API Route

1. Create `apps/api/app/routes/your_route.py` with an `APIRouter`
2. Use `prefix="/v1/your-resource"` and `tags=["your-resource"]`
3. Add auth dependency: `Depends(get_business_user)` for user-facing, `Depends(require_internal_key)` for internal
4. Register in `apps/api/app/main.py` via `app.include_router()`
5. Document in `docs/API_REFERENCE.md`

### Adding a Database Migration

1. Create `supabase/migrations/NNNN_description.sql` (next sequential number)
2. Use `CREATE OR REPLACE` for functions, `IF NOT EXISTS` for tables
3. Apply to the live Supabase database via `psql`
4. Update `docs/DATABASE.md` if adding tables or RPCs
5. Consider whether the change should also be folded into `schema.sql` for fresh bootstraps

### Adding a Dashboard Page

1. Create a directory under `apps/dashboard/src/app/(app)/your-page/`
2. Add `page.tsx` as the main component
3. Use `apiFetch()` from `src/lib/api.ts` for API calls
4. Add navigation in `src/app/(app)/layout.tsx` sidebar

## Branch and PR Workflow

1. Create a feature branch from `main`
2. Make changes with clear, atomic commits
3. Ensure CI passes (Python import checks, TypeScript build, Docker build)
4. Open a PR with a description of what changed and why
5. Merge to `main` after review

## Commit Messages

Follow the existing project convention:

- **First line**: Short summary of what and why (not how)
- **Body** (for non-trivial changes): Bullet-point list of specific changes grouped by category
- Use imperative mood ("Add", "Fix", "Update", not "Added", "Fixed")

Examples from the project history:

```
Harden all external service integrations (17 fixes across security, correctness, resilience)

Critical — Security & Data Correctness:
1. SSRF protection in web scraper: private-IP blocklist, queue cap, response size cap
2. Booking reschedule fix: update_event called with wrong kwargs
...
```

```
Implement Phase 4 (Booking) + Phase 5 (Reactivation + ROI) backend
```

## Conventions

- **trace_id**: Every webhook -> event -> job -> action chain carries a `trace_id` for end-to-end tracing. Always propagate it.
- **Idempotency**: Use `idempotency_key` on every `enqueue_job` and `enqueue_event` call. Follow the patterns in `workflows/queue_workflow_pack.yaml`.
- **Error handling**: Raise `PermanentError` for non-retryable failures and `RetryableError` for transient issues. Unhandled exceptions are retried with exponential backoff.
- **RLS awareness**: Workers use the service role key (bypasses RLS). API routes rely on RLS + `business_id` scoping. Never expose the service key to the client.
- **No secrets in code**: All credentials come from environment variables. Never hardcode API keys, tokens, or passwords.

## Documentation

When making changes:

- Update `docs/API_REFERENCE.md` for new or changed endpoints
- Update `docs/DATABASE.md` for schema changes
- Update `docs/ENV_VARS.md` for new environment variables
- Update `workflows/QUEUE_WORKFLOWS.md` for new queues or changed contracts
- Keep module-level docstrings accurate
