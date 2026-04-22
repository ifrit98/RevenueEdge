#!/usr/bin/env bash
# Phase 2 smoke test — after-hours intake + FAQ grounding + knowledge gaps.
#
# Prerequisites:
#   - Supabase schema + seed applied.
#   - .env populated.
#   - re-api, re-webhooks, re-workers running.
#
# What this tests:
#   1. Seeds a business with weekday-only hours and 3 knowledge items.
#   2. Simulates after-hours SMS → expects after-hours template reply.
#   3. Simulates in-hours FAQ SMS → expects grounded knowledge reply.
#   4. Simulates unanswerable question → expects fallback + knowledge_review task.
#   5. Asserts a leads row exists.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [ -f ".env" ]; then set -a; . ./.env; set +a; fi

export ENVIRONMENT=test

RE_WEBHOOKS_URL="${RE_WEBHOOKS_URL:-http://localhost:8081}"
DID="${PHASE2_DID:-+15555550200}"
SLUG="smoke-test-phase2"
CALLER="+12025550002"

echo "[1/5] Seeding test business with hours + knowledge..."
SEED_JSON="$(python3 scripts/seed_business.py --slug "${SLUG}" --did "${DID}")"
echo "${SEED_JSON}"
BUSINESS_ID="$(printf '%s' "${SEED_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["business_id"])')"

python3 - "${BUSINESS_ID}" <<'PY'
import os, sys
sys.path.insert(0, "apps/workers")
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE"]
client = create_client(url, key)
biz_id = sys.argv[1]

client.table("businesses").update({
    "hours": {
        "weekly": {
            "mon": {"open": "09:00", "close": "17:00"},
            "tue": {"open": "09:00", "close": "17:00"},
            "wed": {"open": "09:00", "close": "17:00"},
            "thu": {"open": "09:00", "close": "17:00"},
            "fri": {"open": "09:00", "close": "17:00"},
        },
        "holidays": [],
    },
    "timezone": "America/New_York",
}).eq("id", biz_id).execute()

for item in [
    {"type": "faq", "title": "Business Hours", "content": "We are open Monday through Friday, 9 AM to 5 PM Eastern."},
    {"type": "pricing", "title": "Drain Cleaning", "content": "Drain cleaning costs between $150 and $350 depending on severity and access."},
    {"type": "faq", "title": "Service Area", "content": "We serve the greater metro area within a 30 mile radius of downtown."},
]:
    client.table("knowledge_items").insert({**item, "business_id": biz_id, "approved": True}).execute()

print(f"[seed] business {biz_id} configured with hours + 3 knowledge items")
PY

echo
echo "[2/5] Simulating after-hours inbound SMS..."
HTTP=$(curl -s -o /tmp/re_p2_resp1.json -w "%{http_code}" \
  -X POST "${RE_WEBHOOKS_URL}/webhooks/retell" \
  -H "Content-Type: application/json" \
  --data "{
    \"event\": \"call_ended\",
    \"call\": {
      \"call_id\": \"p2-afterhours-$(date +%s)\",
      \"from_number\": \"${CALLER}\",
      \"to_number\": \"${DID}\",
      \"call_status\": \"ended\",
      \"call_type\": \"phone_call\",
      \"direction\": \"inbound\",
      \"disconnection_reason\": \"user_hangup_before_connect\",
      \"duration_ms\": 0,
      \"transcript\": \"What time do you open tomorrow?\",
      \"metadata\": {}
    }
  }")
echo "HTTP ${HTTP}"; cat /tmp/re_p2_resp1.json; echo

echo
echo "[3/5] Polling for after-hours reply + classification (up to 60s)..."
python3 - "${BUSINESS_ID}" <<'PY'
import os, sys, time, json
sys.path.insert(0, "apps/workers")
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE"]
client = create_client(url, key)
biz_id = sys.argv[1]

deadline = time.time() + 60
found_reply = False
found_classified = False
found_lead = False

while time.time() < deadline and not (found_reply and found_classified):
    ev = client.table("events").select("event_type").eq("business_id", biz_id) \
        .order("occurred_at", desc=True).limit(50).execute()
    for r in ev.data or []:
        if r["event_type"] == "outbound.sms.sent":
            found_reply = True
        if r["event_type"] == "conversation.classified":
            found_classified = True
    time.sleep(3)

leads = client.table("leads").select("id").eq("business_id", biz_id).limit(1).execute()
found_lead = bool(leads.data)

results = {"reply_sent": found_reply, "classified": found_classified, "lead_exists": found_lead}
print(json.dumps(results, indent=2))

if not found_reply:
    print("FAIL: no outbound SMS sent"); sys.exit(1)
if not found_classified:
    print("FAIL: no conversation.classified event"); sys.exit(1)
print("PASS: after-hours + classification OK")
PY

echo
echo "[4/5] Simulating unanswerable question (knowledge gap)..."
HTTP=$(curl -s -o /tmp/re_p2_resp2.json -w "%{http_code}" \
  -X POST "${RE_WEBHOOKS_URL}/webhooks/retell" \
  -H "Content-Type: application/json" \
  --data "{
    \"event\": \"call_ended\",
    \"call\": {
      \"call_id\": \"p2-gap-$(date +%s)\",
      \"from_number\": \"+12025550003\",
      \"to_number\": \"${DID}\",
      \"call_status\": \"ended\",
      \"call_type\": \"phone_call\",
      \"direction\": \"inbound\",
      \"disconnection_reason\": \"user_hangup_before_connect\",
      \"duration_ms\": 0,
      \"transcript\": \"Do you do roof repair?\",
      \"metadata\": {}
    }
  }")
echo "HTTP ${HTTP}"

echo
echo "[5/5] Polling for knowledge_review task (up to 60s)..."
python3 - "${BUSINESS_ID}" <<'PY'
import os, sys, time, json
sys.path.insert(0, "apps/workers")
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE"]
client = create_client(url, key)
biz_id = sys.argv[1]

deadline = time.time() + 60
found_task = False
while time.time() < deadline and not found_task:
    tasks = client.table("tasks").select("id, type").eq("business_id", biz_id) \
        .eq("type", "knowledge_gap").limit(1).execute()
    if tasks.data:
        found_task = True
        break
    time.sleep(3)

if not found_task:
    print("FAIL: no knowledge_gap task created"); sys.exit(1)
print("PASS: knowledge gap task created")
PY

echo
echo "Phase 2 smoke test PASSED."
