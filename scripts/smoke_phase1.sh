#!/usr/bin/env bash
# Phase 1 smoke test — end-to-end missed-call recovery happy path.
#
# Prerequisites:
#   - Supabase schema applied (supabase/schema.sql + seed_mvp_defaults.sql).
#   - .env populated with SUPABASE_URL / SUPABASE_SERVICE_KEY and (ideally)
#     RETELL_FROM_NUMBER / INTERNAL_SERVICE_KEY.
#   - `re-api`, `re-webhooks`, and `re-workers` running (or use
#     scripts/run_api.sh etc. in three terminals).
#
# What this script does:
#   1. Seeds a test business + phone/SMS channel for `+15555550123`.
#   2. POSTs a synthetic Retell `call_ended` (missed) webhook to re-webhooks.
#   3. Polls for:
#        - an `outbound.sms.sent` event on that business
#        - a `conversation.classified` event
#        - at least one task (handoff) or an SMS reply (depending on CI result)
#
# The script is intentionally read-only-except-for-seed: it does not mutate
# existing production state.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

export ENVIRONMENT=test

RE_WEBHOOKS_URL="${RE_WEBHOOKS_URL:-http://localhost:8081}"
RE_API_URL="${RE_API_URL:-http://localhost:8080}"
CALLER_NUMBER="${CALLER_NUMBER:-+12025550001}"
DID="${PHASE1_DID:-+15555550123}"
SLUG="${PHASE1_SLUG:-smoke-test-plumbing}"

echo "[1/4] Seeding test business + channels..."
SEED_JSON="$(python scripts/seed_business.py --slug "${SLUG}" --did "${DID}")"
echo "${SEED_JSON}"
BUSINESS_ID="$(printf '%s' "${SEED_JSON}" | python -c 'import sys, json; print(json.load(sys.stdin)["business_id"])')"
echo "business_id=${BUSINESS_ID}"

CALL_ID="smoke-$(date +%s)"
TRACE_ID="smoke-trace-$(date +%s)"
PAYLOAD=$(cat <<JSON
{
  "event": "call_ended",
  "call": {
    "call_id": "${CALL_ID}",
    "from_number": "${CALLER_NUMBER}",
    "to_number": "${DID}",
    "call_status": "ended",
    "call_type": "phone_call",
    "direction": "inbound",
    "disconnection_reason": "user_hangup_before_connect",
    "duration_ms": 1200,
    "transcript": "",
    "metadata": { "trace_id": "${TRACE_ID}" }
  }
}
JSON
)

echo
echo "[2/4] Posting synthetic missed-call webhook → ${RE_WEBHOOKS_URL}/webhooks/retell"
HTTP_CODE=$(curl -s -o /tmp/re_webhook_resp.json -w "%{http_code}" \
  -X POST "${RE_WEBHOOKS_URL}/webhooks/retell" \
  -H "Content-Type: application/json" \
  --data "${PAYLOAD}")
cat /tmp/re_webhook_resp.json; echo
if [ "${HTTP_CODE}" != "200" ]; then
  echo "FAIL: webhook returned HTTP ${HTTP_CODE}"; exit 1
fi

echo
echo "[3/4] Polling Supabase for downstream effects (up to 60s)..."
python - <<'PY'
import os, sys, time, json
sys.path.insert(0, "apps/workers")
from supabase import create_client  # type: ignore

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE"]
client = create_client(url, key)
business_slug = os.environ.get("PHASE1_SLUG", "smoke-test-plumbing")

biz = client.table("businesses").select("id").eq("slug", business_slug).limit(1).execute()
if not biz.data:
    print("FAIL: seeded business not found"); sys.exit(1)
business_id = biz.data[0]["id"]

deadline = time.time() + 60
outcomes = {"outbound_sent": False, "classified": False, "task_or_reply": False}
while time.time() < deadline and not all(outcomes.values()):
    ev = client.table("events").select("event_type, payload, occurred_at") \
        .eq("business_id", business_id) \
        .order("occurred_at", desc=True).limit(50).execute()
    for row in ev.data or []:
        et = row.get("event_type", "")
        if et == "outbound.sms.sent":
            outcomes["outbound_sent"] = True
        if et == "conversation.classified":
            outcomes["classified"] = True
        if et == "conversation.handoff_created":
            outcomes["task_or_reply"] = True
    if not outcomes["task_or_reply"]:
        msgs = client.table("messages").select("id").eq("business_id", business_id) \
            .eq("direction", "outbound").limit(1).execute()
        if msgs.data:
            outcomes["task_or_reply"] = True
    time.sleep(3)

print(json.dumps(outcomes, indent=2))
if not outcomes["outbound_sent"]:
    print("FAIL: no outbound.sms.sent event observed"); sys.exit(1)
if not outcomes["classified"]:
    print("FAIL: no conversation.classified event observed"); sys.exit(1)
if not outcomes["task_or_reply"]:
    print("FAIL: no downstream task or reply observed"); sys.exit(1)
print("PASS: missed-call recovery happy path")
PY

echo
echo "[4/4] Smoke test complete."
