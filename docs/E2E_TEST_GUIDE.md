# End-to-End Test Guide

Manual walkthrough for verifying the entire Revenue Edge stack with a single
test client. No external API keys are required — everything runs in dry-run /
heuristic-fallback mode by default.

---

## Prerequisites

| Requirement              | Check                                        |
|--------------------------|----------------------------------------------|
| Python 3.11+             | `python3 --version`                          |
| Node.js 20+              | `node --version`                             |
| `.env` configured        | Supabase keys + `ENVIRONMENT=test`           |
| Schema applied           | Migrations 0001–0004 applied to Supabase     |
| Virtual env active       | `source .venv-re/bin/activate`               |

## Quick Start (5 minutes)

```bash
# 1. Terminal 1 — API
source .env && python -m uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8080

# 2. Terminal 2 — Webhooks
source .env && python -m uvicorn apps.webhooks.app.main:app --host 0.0.0.0 --port 8081

# 3. Terminal 3 — Workers
source .env && python -m apps.workers.src.main

# 4. Terminal 4 — Dashboard
cd apps/dashboard && npm run dev

# 5. Terminal 5 — Setup + test
source .env && python scripts/setup_test_account.py
./scripts/simulate_webhook.sh full auto
```

---

## Step-by-Step Walkthrough

### Step 1: Configure Environment

Your `.env` should have:

```
ENVIRONMENT=test
RE_API_URL=http://localhost:8080
RE_WEBHOOKS_URL=http://localhost:8081
```

`ENVIRONMENT=test` does two things:
- Skips Retell webhook signature verification (so curl works)
- Relaxes required env var checks on API startup

### Step 2: Start Services

Start each in its own terminal from the repo root:

**API Gateway** (port 8080):
```bash
source .venv-re/bin/activate && source .env
python -m uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8080 --reload
```

**Webhook Service** (port 8081):
```bash
source .venv-re/bin/activate && source .env
python -m uvicorn apps.webhooks.app.main:app --host 0.0.0.0 --port 8081 --reload
```

**Workers**:
```bash
source .venv-re/bin/activate && source .env
python -m apps.workers.src.main
```

**Dashboard** (port 3000):
```bash
cd apps/dashboard
npm install  # first time only
npm run dev
```

Verify health:
```bash
curl http://localhost:8080/health
curl http://localhost:8081/health
```

### Step 3: Create Test Account

```bash
source .venv-re/bin/activate && source .env
python scripts/setup_test_account.py
```

This creates:
- A Supabase Auth user (`test@revenueedge.local` / `testing123!`)
- A business "Acme Home Services" with voice + SMS channels
- Plumbing service presets
- Sample knowledge items (FAQs, policies)
- A `business_members` row linking the user as owner

The script prints login credentials, business_id, and test commands.
Details are also saved to `test_account.json`.

**Custom options:**
```bash
python scripts/setup_test_account.py \
  --email owner@acme.com \
  --password mypass123 \
  --business-name "Acme HVAC" \
  --slug acme-hvac \
  --services hvac
```

### Step 4: Log In to Dashboard

1. Open http://localhost:3000/login
2. Enter the email and password from step 3
3. You should see the dashboard with the test business

**What to verify:**
- Settings page shows business info
- Services page shows the seeded service presets
- Knowledge page shows the seeded FAQ/policy items
- Conversations page is empty (no inbound events yet)
- Leads page is empty

### Step 5: Simulate Inbound Events

Use the webhook simulator to trigger the full pipeline:

**Missed call** (triggers missed-call recovery SMS):
```bash
./scripts/simulate_webhook.sh missed-call auto
```

**Inbound SMS** (triggers conversation intelligence):
```bash
./scripts/simulate_webhook.sh sms auto "I need a plumber, my kitchen sink is leaking"
```

**Completed call** (full transcript + analysis):
```bash
./scripts/simulate_webhook.sh call-ended auto
```

**Full scenario** (missed call → SMS → call → booking):
```bash
./scripts/simulate_webhook.sh full auto
```

### Step 6: Verify the Pipeline

After simulating events, check each layer:

#### 6a. Worker Logs

Watch the workers terminal. You should see:

```
INFO  Inbound event normalized  event_type=call.missed ...
INFO  [SMS][dry-run] ...                  ← SMS dry-run (no API key)
INFO  Conversation intelligence complete  intent=book_service ...
```

Without `OPENAI_API_KEY`, the LLM falls back to heuristic classification.
Without `RETELL_API_KEY`, SMS shows `[dry-run]` in logs.

#### 6b. Dashboard

Refresh the dashboard and check:
- **Conversations**: New conversation(s) with inbound messages
- **Leads**: Qualified leads with intent classification
- **Contacts**: New contact entry for the simulated caller

#### 6c. Database (Direct)

```bash
# Check queue jobs
curl -s http://localhost:8080/internal/queue/status \
  -H "x-internal-key: re-internal-dev-key-change-me" | python3 -m json.tool

# Check conversations via API (uses ENVIRONMENT=test header fallback)
BUSINESS_ID=$(python3 -c "import json; print(json.load(open('test_account.json'))['business_id'])")
curl -s "http://localhost:8080/conversations?limit=10" \
  -H "x-business-id: $BUSINESS_ID" \
  -H "x-user-id: test" | python3 -m json.tool
```

### Step 7: Test Dashboard Interactions

With data in the system, test interactive features:

1. **Conversation detail**: Click a conversation to see messages, classification
2. **Lead management**: Try changing lead status (open → contacted)
3. **Task creation**: Create a follow-up task from a conversation
4. **Knowledge management**: Add a new FAQ item via the Knowledge page
5. **Service editing**: Edit a service's pricing or description

---

## Testing With Real API Keys (Optional)

### OpenAI Only (Real LLM Classification)

Add to `.env`:
```
OPENAI_API_KEY=sk-...
```

Restart workers. Now `conversation-intelligence` will use GPT for intent
classification instead of heuristics. Verify by checking that leads have
more precise `intent` and `confidence` values.

### Retell + Twilio (Real SMS)

Add to `.env`:
```
RETELL_API_KEY=key_...
RETELL_FROM_NUMBER=+1...
```

Now missed-call recovery will actually send SMS. **Be careful with real
phone numbers** — use your own number for testing.

### Full Stack

Add all keys (see `docs/ENV_VARS.md` for the full list). With all keys
configured, the system operates identically to production except
`ENVIRONMENT=test` still skips webhook signature verification.

---

## Troubleshooting

### "User is not associated with any business" (403)

The `business_members` row is missing. Re-run `setup_test_account.py` or
insert manually:

```sql
insert into business_members (business_id, user_id, role)
values ('<business_id>', '<user_id>', 'owner');
```

### Webhook returns 401

`ENVIRONMENT` is not set to `test`. Check `.env` and restart the webhooks
service.

### No worker activity after webhook

1. Verify the API is running (`curl localhost:8080/health`)
2. Check that `RE_API_URL` in `.env` is `http://localhost:8080` (not
   `http://re-api:8080` which is the Docker hostname)
3. Check the webhooks service logs for enqueue errors

### Dashboard shows "Loading..." forever

1. Verify `apps/dashboard/.env.local` has the correct `NEXT_PUBLIC_API_URL`
2. Check browser DevTools console/network for CORS or 401 errors
3. Ensure the API service is running

### Queue jobs stuck in "pending"

Workers might not be running, or they might not be picking up the right
queue. Check worker startup logs — each registered worker and its queue name
should be printed at boot.

### "seed_revenue_edge_mvp_defaults failed"

The RPC may not exist in your schema. Ensure all migrations (0001–0004) have
been applied:

```bash
psql "$DATABASE_URL" -f supabase/migrations/0001_initial.sql
psql "$DATABASE_URL" -f supabase/migrations/0002_metric_rollups.sql
psql "$DATABASE_URL" -f supabase/migrations/0003_photo_upload_tokens.sql
psql "$DATABASE_URL" -f supabase/migrations/0004_hardening.sql
```

---

## Checklist

Use this to confirm every layer works:

- [ ] API health: `curl localhost:8080/health` → 200
- [ ] Webhooks health: `curl localhost:8081/health` → 200
- [ ] Workers printing poll logs
- [ ] Dashboard loads at localhost:3000
- [ ] Login with test credentials works
- [ ] Settings page shows business info
- [ ] Simulate missed call → worker logs show processing
- [ ] Simulate SMS → conversation appears in dashboard
- [ ] Simulate completed call → lead appears with classification
- [ ] Full scenario → all conversation/lead/contact data visible
- [ ] Knowledge page shows seeded items
- [ ] Services page shows seeded presets
