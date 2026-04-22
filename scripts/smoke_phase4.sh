#!/usr/bin/env bash
# Phase 4 smoke test — booking request → callback fallback (no real GCal).
#
# Without a real Google Calendar OAuth connection the booking worker falls
# back to creating a callback task + notifying the customer.  This test
# validates that path end-to-end.
#
# Prerequisites:
#   - Supabase schema + seed, services running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [ -f ".env" ]; then set -a; . ./.env; set +a; fi
export ENVIRONMENT=test

RE_WEBHOOKS_URL="${RE_WEBHOOKS_URL:-http://localhost:8081}"
DID="${PHASE4_DID:-+15555550400}"
SLUG="smoke-test-phase4"
CALLER="+12025550020"

echo "[1/4] Seeding business with booking_automation_enabled + service..."
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

client.table("businesses").update({
    "settings": {"booking_automation_enabled": True},
    "timezone": "America/New_York",
}).eq("id", biz_id).execute()

client.table("services").insert({
    "business_id": biz_id,
    "name": "General Inspection",
    "description": "Home inspection visit",
    "base_price_low": 100,
    "base_price_high": 200,
    "required_intake_fields": ["name", "address"],
    "active": True,
    "tags": ["inspection"],
}).execute()
print(f"[seed] business {biz_id} configured for booking test")
PY

echo
echo "[2/4] Simulating booking request..."
curl -s -o /tmp/re_p4_resp.json -w "HTTP %{http_code}\n" \
  -X POST "${RE_WEBHOOKS_URL}/webhooks/retell" \
  -H "Content-Type: application/json" \
  --data "{
    \"event\": \"call_ended\",
    \"call\": {
      \"call_id\": \"p4-book-$(date +%s)\",
      \"from_number\": \"${CALLER}\",
      \"to_number\": \"${DID}\",
      \"call_status\": \"ended\",
      \"call_type\": \"phone_call\",
      \"direction\": \"inbound\",
      \"disconnection_reason\": \"agent_hangup\",
      \"duration_ms\": 5000,
      \"transcript\": \"Hi, I am Jane Doe at 456 Oak Ave. Can you come Thursday morning for an inspection?\",
      \"metadata\": {}
    }
  }"

echo
echo "[3/4] Polling for booking or callback task (up to 90s)..."
python3 - "${BUSINESS_ID}" <<'PY'
import os, sys, time, json
sys.path.insert(0, "apps/workers")
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE"]
client = create_client(url, key)
biz_id = sys.argv[1]

deadline = time.time() + 90
found_booking = False
found_callback = False
found_reply = False

while time.time() < deadline:
    bookings = client.table("bookings").select("id, status").eq("business_id", biz_id).limit(1).execute()
    if bookings.data:
        found_booking = True

    tasks = client.table("tasks").select("id, type").eq("business_id", biz_id) \
        .eq("type", "callback").limit(1).execute()
    if tasks.data:
        found_callback = True

    ev = client.table("events").select("event_type").eq("business_id", biz_id) \
        .order("occurred_at", desc=True).limit(50).execute()
    for r in ev.data or []:
        if r["event_type"] in ("outbound.sms.sent", "booking.confirmed"):
            found_reply = True

    if (found_booking or found_callback) and found_reply:
        break
    time.sleep(5)

results = {
    "booking_created": found_booking,
    "callback_task": found_callback,
    "customer_notified": found_reply,
}
print(json.dumps(results, indent=2))

if not found_booking and not found_callback:
    print("FAIL: neither booking nor callback task created"); sys.exit(1)
if not found_reply:
    print("FAIL: customer was not notified"); sys.exit(1)

if found_booking:
    print("PASS: booking created + customer notified")
elif found_callback:
    print("PASS: callback fallback + customer notified (no GCal connected)")
PY

echo
echo "[4/4] Phase 4 smoke test PASSED."
