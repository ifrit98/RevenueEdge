#!/usr/bin/env bash
# Phase 3 smoke test — quote intake, draft, approve, send, follow-up.
#
# Prerequisites:
#   - Supabase schema + seed applied, re-api + re-webhooks + re-workers running.
#
# What this tests:
#   1. Seeds a business with one service + required intake fields.
#   2. Simulates quote-request SMS → intelligence classifies, asks for fields.
#   3. Simulates reply with missing fields → intake_fields populated.
#   4. Asserts: quotes row (awaiting_review) + quote_review task.
#   5. Approves quote via API → asserts outbound SMS + follow-up enqueued.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [ -f ".env" ]; then set -a; . ./.env; set +a; fi
export ENVIRONMENT=test

RE_WEBHOOKS_URL="${RE_WEBHOOKS_URL:-http://localhost:8081}"
RE_API_URL="${RE_API_URL:-http://localhost:8080}"
DID="${PHASE3_DID:-+15555550300}"
SLUG="smoke-test-phase3"
CALLER="+12025550010"

echo "[1/6] Seeding business with service..."
SEED_JSON="$(python3 scripts/seed_business.py --slug "${SLUG}" --did "${DID}")"
BUSINESS_ID="$(printf '%s' "${SEED_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["business_id"])')"
echo "business_id=${BUSINESS_ID}"

python3 - "${BUSINESS_ID}" <<'PY'
import os, sys
sys.path.insert(0, "apps/workers")
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE"]
client = create_client(url, key)
biz_id = sys.argv[1]

client.table("services").insert({
    "business_id": biz_id,
    "name": "Drain Cleaning",
    "description": "Residential drain unclogging",
    "base_price_low": 150,
    "base_price_high": 350,
    "required_intake_fields": ["name", "address", "scope"],
    "active": True,
    "tags": ["plumbing"],
}).execute()
print(f"[seed] service created for {biz_id}")
PY

echo
echo "[2/6] Simulating inbound: quote request..."
curl -s -o /tmp/re_p3_resp1.json -w "HTTP %{http_code}\n" \
  -X POST "${RE_WEBHOOKS_URL}/webhooks/retell" \
  -H "Content-Type: application/json" \
  --data "{
    \"event\": \"call_ended\",
    \"call\": {
      \"call_id\": \"p3-quote-$(date +%s)\",
      \"from_number\": \"${CALLER}\",
      \"to_number\": \"${DID}\",
      \"call_status\": \"ended\",
      \"call_type\": \"phone_call\",
      \"direction\": \"inbound\",
      \"disconnection_reason\": \"user_hangup_before_connect\",
      \"duration_ms\": 2000,
      \"transcript\": \"I need a quote for a drain cleaning please\",
      \"metadata\": {}
    }
  }"

echo
echo "[3/6] Waiting for classification + field-ask (30s)..."
sleep 15

echo "Simulating reply with fields..."
curl -s -o /tmp/re_p3_resp2.json -w "HTTP %{http_code}\n" \
  -X POST "${RE_WEBHOOKS_URL}/webhooks/retell" \
  -H "Content-Type: application/json" \
  --data "{
    \"event\": \"call_ended\",
    \"call\": {
      \"call_id\": \"p3-reply-$(date +%s)\",
      \"from_number\": \"${CALLER}\",
      \"to_number\": \"${DID}\",
      \"call_status\": \"ended\",
      \"call_type\": \"phone_call\",
      \"direction\": \"inbound\",
      \"disconnection_reason\": \"agent_hangup\",
      \"duration_ms\": 3000,
      \"transcript\": \"My name is John Smith, address is 123 Main St, the kitchen sink is badly clogged\",
      \"metadata\": {}
    }
  }"

echo
echo "[4/6] Polling for quote + task (up to 90s)..."
python3 - "${BUSINESS_ID}" <<'PY'
import os, sys, time, json
sys.path.insert(0, "apps/workers")
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE"]
client = create_client(url, key)
biz_id = sys.argv[1]

deadline = time.time() + 90
found_quote = None
found_task = False
while time.time() < deadline:
    quotes = client.table("quotes").select("id, status").eq("business_id", biz_id).limit(1).execute()
    if quotes.data and quotes.data[0]["status"] == "awaiting_review":
        found_quote = quotes.data[0]
    tasks = client.table("tasks").select("id, type").eq("business_id", biz_id) \
        .eq("type", "quote_review").limit(1).execute()
    if tasks.data:
        found_task = True
    if found_quote and found_task:
        break
    time.sleep(5)

print(json.dumps({"quote": found_quote, "task_found": found_task}, indent=2, default=str))
if not found_quote:
    print("FAIL: no awaiting_review quote"); sys.exit(1)
if not found_task:
    print("FAIL: no quote_review task"); sys.exit(1)

with open("/tmp/re_p3_quote_id.txt", "w") as f:
    f.write(found_quote["id"])
print("PASS: quote drafted + review task created")
PY

echo
echo "[5/6] Approving quote via API..."
QUOTE_ID=$(cat /tmp/re_p3_quote_id.txt)
HTTP=$(curl -s -o /tmp/re_p3_approve.json -w "%{http_code}" \
  -X POST "${RE_API_URL}/v1/quotes/${QUOTE_ID}/approve" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${SUPABASE_SERVICE_KEY:-${SUPABASE_SERVICE_ROLE}}" \
  -H "x-business-id: ${BUSINESS_ID}")
echo "HTTP ${HTTP}"; cat /tmp/re_p3_approve.json; echo

echo
echo "[6/6] Polling for quote send + follow-up (up to 60s)..."
python3 - "${BUSINESS_ID}" <<'PY'
import os, sys, time, json
sys.path.insert(0, "apps/workers")
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE"]
client = create_client(url, key)
biz_id = sys.argv[1]

deadline = time.time() + 60
sent = False
followup = False
while time.time() < deadline:
    ev = client.table("events").select("event_type").eq("business_id", biz_id) \
        .order("occurred_at", desc=True).limit(50).execute()
    for r in ev.data or []:
        if r["event_type"] == "quote.sent":
            sent = True
    jobs = client.table("queue_jobs").select("id, queue, payload").eq("business_id", biz_id) \
        .eq("queue", "follow-up-scheduler").limit(5).execute()
    for j in jobs.data or []:
        p = j.get("payload") or {}
        if p.get("reason") == "quote_recovery":
            followup = True
    if sent and followup:
        break
    time.sleep(5)

print(json.dumps({"quote_sent": sent, "followup_scheduled": followup}, indent=2))
if not sent:
    print("FAIL: no quote.sent event"); sys.exit(1)
if not followup:
    print("FAIL: no quote_recovery follow-up"); sys.exit(1)
print("PASS: quote approved, sent, follow-up scheduled")
PY

echo
echo "Phase 3 smoke test PASSED."
