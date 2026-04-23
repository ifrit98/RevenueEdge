# Environment Variables Reference

All services read from a shared `.env` file in development. In production, variables are passed explicitly (no `.env` file).

## Supabase

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `SUPABASE_URL` | Yes | api, webhooks, workers, dashboard | -- | Supabase project URL (e.g., `https://xxx.supabase.co`) |
| `SUPABASE_ANON_KEY` | Yes | dashboard | -- | Supabase anonymous/public key for client-side auth |
| `SUPABASE_SERVICE_KEY` | Yes | api, webhooks, workers | -- | Service role key (bypasses RLS); keep secret |
| `SUPABASE_JWT_SECRET` | Yes | api | -- | JWT secret for verifying Supabase tokens |
| `SUPABASE_DB_PASSWORD` | No | scripts | -- | Database password for `psql` in bootstrap/migration scripts |

## Retell AI

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `RETELL_API_KEY` | Yes* | webhooks, workers | -- | API key for Retell; used for webhook signature verification and SMS sending |
| `RETELL_WEBHOOK_SECRET` | No | webhooks | -- | Separate webhook secret (falls back to `RETELL_API_KEY` if unset) |
| `RETELL_FROM_NUMBER` | No | workers | -- | Default SMS from-number if channel config doesn't specify one |

*Required for production. Development works without it (webhook verification is skipped in `test` environment).

## Twilio (Fallback)

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `TWILIO_ACCOUNT_SID` | No | workers | -- | Twilio account SID for SMS fallback |
| `TWILIO_AUTH_TOKEN` | No | workers | -- | Twilio auth token |
| `TWILIO_MESSAGING_SERVICE_SID` | No | workers | -- | Messaging service SID for compliant sending |

Twilio is only used when Retell SMS fails. All three must be set together for the fallback to work.

## Email (SendGrid)

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `SENDGRID_API_KEY` | No | workers | -- | SendGrid API key for transactional email |
| `DEFAULT_EMAIL_FROM` | No | workers | `no-reply@revenueedge.local` | Default sender email address |

When unset, email operations log a dry-run message instead of sending.

## LLM (OpenAI)

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes* | workers | -- | OpenAI API key for chat completion and embeddings |
| `LLM_CHAT_MODEL` | No | workers | `gpt-4.1-mini` | Model for conversation intelligence and quote drafting |
| `LLM_EMBEDDING_MODEL` | No | workers | `text-embedding-3-small` | Model for knowledge item embeddings (1536 dimensions) |

*Required for production. Without it, conversation intelligence uses a heuristic fallback classifier.

## Google Calendar

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `GOOGLE_CLIENT_ID` | No | api, workers | -- | OAuth 2.0 client ID for Calendar integration |
| `GOOGLE_CLIENT_SECRET` | No | api, workers | -- | OAuth 2.0 client secret |

Both must be set for the booking integration to work. Without them, booking requests fall back to callback tasks.

## Internal Auth

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `INTERNAL_SERVICE_KEY` | Yes | webhooks, workers, api | -- | Shared secret for service-to-service authentication |
| `INTERNAL_TOOL_USER_ID` | No | workers | -- | Fallback `auth.users` UUID for audited writes from workers |

## Application

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `ENVIRONMENT` | No | all | `development` | Environment name (`development`, `test`, `production`) |
| `RELEASE` | No | all | `local` | Release identifier for Sentry |
| `LOG_LEVEL` | No | all | `INFO` | Python logging level |
| `CORS_ALLOWED_ORIGINS` | No | api | `http://localhost:3000` | Comma-separated list of allowed CORS origins |

## Observability

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `SENTRY_DSN` | No | api, workers | -- | Sentry DSN for error reporting |
| `SENTRY_SAMPLE_RATE` | No | api, workers | `1.0` | Error event sample rate (0.0-1.0) |
| `SENTRY_TRACES_SAMPLE_RATE` | No | api, workers | `0.1` | Performance trace sample rate |
| `SENTRY_DEV_ENABLED` | No | api, workers | `false` | Enable Sentry in development |

## Worker Tunables

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `WORKERS` | Yes | workers | -- | Comma-separated list of workers to run (e.g., `inbound_normalizer,conversation_intelligence,outbound_action,handoff,followup_scheduler,knowledge_ingestion,quote_drafting,booking`) |
| `WORKER_POLL_INTERVAL_SECONDS` | No | workers | `2` | Seconds between poll cycles when no jobs found |
| `WORKER_CLAIM_BATCH_SIZE` | No | workers | `5` | Max jobs to claim per poll cycle |
| `WORKER_LOCK_TIMEOUT_SECONDS` | No | workers | `300` | Lock duration in seconds before a job is considered stale |

## Service URLs

| Variable | Required | Services | Default | Description |
|----------|----------|----------|---------|-------------|
| `RE_API_URL` | No | webhooks, workers | `http://re-api:8080` | Internal URL for the API service |
| `RE_WEBHOOKS_URL` | No | api | `http://re-webhooks:8081` | Internal URL for the webhooks service |

These default to Docker Compose service names. Override when running services individually (e.g., `http://localhost:8080`).

## Dashboard (Build-Time)

These are set as Next.js build-time environment variables, not runtime:

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Yes | Supabase project URL (client-side) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Yes | Supabase anon key (client-side) |
| `NEXT_PUBLIC_API_URL` | No | API URL for dashboard API calls (default: `http://localhost:8080`) |
