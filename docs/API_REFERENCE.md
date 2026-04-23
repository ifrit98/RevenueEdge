# API Reference

Base URL: `http://localhost:8080` (dev) or `https://yourdomain.com/v1` (production via Caddy).

## Authentication

Most endpoints require a Supabase JWT in the `Authorization: Bearer <token>` header and a `x-business-id` header identifying the business context. These are validated by the `get_business_user` dependency, which confirms the user is a member of the specified business.

Internal endpoints use `X-Internal-Key` checked against the `INTERNAL_SERVICE_KEY` environment variable.

Public endpoints (health, uploads, OAuth callback) require no authentication.

---

## Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | None | Service name and API version |
| GET | `/health` | None | Liveness probe (process is up) |
| GET | `/ready` | None | Readiness probe (Supabase reachable) |

## Internal Queue

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/internal/queue/enqueue` | Internal key | Enqueue a job via `enqueue_job` RPC |
| GET | `/internal/queue/dead-letter/count` | Internal key | Count dead-letter jobs by queue |

## Businesses

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/businesses` | JWT | List businesses the user belongs to |
| GET | `/v1/businesses/{business_id}` | JWT | Get a business by ID |

## Channels

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/channels` | JWT | List channels (optional type/status filter) |
| GET | `/v1/channels/{channel_id}` | JWT | Get channel detail |
| POST | `/v1/channels` | JWT | Create a channel |
| PATCH | `/v1/channels/{channel_id}` | JWT | Update a channel |
| DELETE | `/v1/channels/{channel_id}` | JWT | Archive a channel (sets status to `archived`) |

## Services

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/services` | JWT | List services (optional active-only filter) |
| GET | `/v1/services/{service_id}` | JWT | Get service detail |
| POST | `/v1/services` | JWT | Create a service |
| PATCH | `/v1/services/{service_id}` | JWT | Update a service |

## Conversations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/conversations` | JWT | List conversations (dashboard view with filters) |
| GET | `/v1/conversations/{conversation_id}` | JWT | Get conversation with messages and contact |

## Leads

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/leads` | JWT | List leads (optional stage/assignee filters) |
| GET | `/v1/leads/{lead_id}` | JWT | Get lead with contact details |
| PATCH | `/v1/leads/{lead_id}` | JWT | Update stage, assignee, or metadata |

## Quotes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/quotes` | JWT | List quotes (optional status filter) |
| GET | `/v1/quotes/{quote_id}` | JWT | Get quote with lead and contact |
| PATCH | `/v1/quotes/{quote_id}` | JWT | Update quote draft, amounts, or terms |
| POST | `/v1/quotes/{quote_id}/approve` | JWT | Approve and enqueue send |
| POST | `/v1/quotes/{quote_id}/decline` | JWT | Void the quote with optional reason |

## Bookings

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/bookings` | JWT | List bookings (filters, pagination) |
| GET | `/v1/bookings/{booking_id}` | JWT | Get booking with contact and service |
| PATCH | `/v1/bookings/{booking_id}` | JWT | Update notes or assignee |
| POST | `/v1/bookings/{booking_id}/cancel` | JWT | Cancel booking and sync to Google Calendar |
| POST | `/v1/bookings/{booking_id}/complete` | JWT | Mark booking completed |
| POST | `/v1/bookings/{booking_id}/reschedule` | JWT | Reschedule and sync to Google Calendar |

## Tasks

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/tasks` | JWT | List tasks (dashboard inbox with filters) |
| PATCH | `/v1/tasks/{task_id}` | JWT | Update status, priority, or description |

## Knowledge Items

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/knowledge` | JWT | List knowledge items (filters, pagination) |
| GET | `/v1/knowledge/{item_id}` | JWT | Get a single knowledge item |
| POST | `/v1/knowledge` | JWT | Create an item and enqueue embedding |
| PATCH | `/v1/knowledge/{item_id}` | JWT | Update an item (re-embeds on content change) |
| DELETE | `/v1/knowledge/{item_id}` | JWT | Soft-delete (sets `active = false`) |

## Knowledge Ingestion

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/knowledge/ingest/website` | JWT | Enqueue website scrape job (max 100 pages) |
| POST | `/v1/knowledge/ingest/document` | JWT | Upload PDF/DOCX/TXT (10 MB max), parse and chunk inline |
| POST | `/v1/knowledge/ingest/google-docs` | JWT | Enqueue Google Docs fetch job by document ID |

### Document Upload

`POST /v1/knowledge/ingest/document` accepts `multipart/form-data`:
- `file` -- The document file (PDF, DOCX, or TXT)
- `type` -- Knowledge type: `faq`, `objection`, `product`, `policy`, or `other`

## Uploads (Photo Request)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/uploads/request-link` | JWT | Create an upload token and return customer-facing URL |
| GET | `/v1/uploads/{token}` | None | Validate token and return Supabase Storage upload details |
| POST | `/v1/uploads/{token}/complete` | None | Mark token used and add inbound message with file URL |

## Reactivation

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/reactivation/preview` | JWT | Preview segment: count and sample of matching stale leads |
| POST | `/v1/reactivation/launch` | JWT | Launch a reactivation campaign (enqueues staggered outbound jobs) |
| GET | `/v1/reactivation/{batch_id}` | JWT | Get batch status: sent/replied counts |
| GET | `/v1/reactivation` | JWT | List recent reactivation batches |

## Metrics

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/metrics` | JWT | List recent metric snapshots for the business |
| GET | `/v1/metrics/comparison` | JWT | Before/after snapshot comparison for ROI metrics |
| POST | `/v1/metrics/rollup` | Internal key | Trigger daily rollup for a date and optional business |

## Integrations

### Google Calendar

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/integrations/google-calendar/auth-url` | JWT | Get Google OAuth authorization URL |
| GET | `/v1/integrations/google-calendar/callback` | None | OAuth redirect: exchange code for tokens |
| GET | `/v1/integrations/google-calendar/status` | JWT | Check if Google Calendar is connected |
| DELETE | `/v1/integrations/google-calendar` | JWT | Disconnect Google Calendar (revoke tokens) |
