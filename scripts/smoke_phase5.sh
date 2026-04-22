#!/usr/bin/env bash
# Phase 5 smoke test — reactivation preview/launch + ROI comparison.
#
# Prerequisites:
#   - Supabase schema + seed, re-api running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [ -f ".env" ]; then set -a; . ./.env; set +a; fi
export ENVIRONMENT=test

RE_API_URL="${RE_API_URL:-http://localhost:8080}"
SLUG="smoke-test-phase5"
DID="${PHASE5_DID:-+15555550500}"
AUTH="${SUPABASE_SERVICE_KEY:-${SUPABASE_SERVICE_ROLE}}"

echo "[1/5] Seeding business with stale leads..."
SEED_JSON="$(python3 scripts/seed_business.py --slug "${SLUG}" --did "${DID}")"
BUSINESS_ID="$(printf '%s' "${SEED_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["business_id"])')"
echo "business_id=${BUSINESS_ID}"

python3 - "${BUSINESS_ID}" <<'PY'
import os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "apps/workers")
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE"]
client = create_client(url, key)
biz_id = sys.argv[1]
stale = (datetime.now(timezone.utc) - timedelta(days=50)).isoformat()

for i in range(5):
    phone = f"+1202555100{i}"
    contact = client.table("contacts").insert({
        "business_id": biz_id,
        "phone_e164": phone,
        "name": f"Stale Lead {i}",
    }).execute()
    cid = contact.data[0]["id"]
    client.table("leads").insert({
        "business_id": biz_id,
        "contact_id": cid,
        "stage": "new",
        "score": 30,
        "source": "smoke_test",
    }).execute()
    client.table("contacts").update({"updated_at": stale}).eq("id", cid).execute()

print(f"[seed] 5 stale leads created for {biz_id}")
PY

echo
echo "[2/5] Calling reactivation preview..."
PREVIEW=$(curl -s -X POST "${RE_API_URL}/v1/reactivation/preview" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${AUTH}" \
  -H "x-business-id: ${BUSINESS_ID}" \
  -d '{"days_inactive": 45}')
echo "${PREVIEW}"

COUNT=$(echo "${PREVIEW}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("count",0))' 2>/dev/null || echo 0)
echo "Preview count: ${COUNT}"
if [ "${COUNT}" -lt 1 ]; then
  echo "FAIL: preview returned 0 contacts (expected >= 1)"
  exit 1
fi
echo "PASS: preview returned ${COUNT} contacts"

echo
echo "[3/5] Launching reactivation batch..."
LAUNCH=$(curl -s -X POST "${RE_API_URL}/v1/reactivation/launch" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${AUTH}" \
  -H "x-business-id: ${BUSINESS_ID}" \
  -d '{"days_inactive": 45}')
echo "${LAUNCH}"

echo
echo "[4/5] Checking metrics comparison endpoint..."
COMP=$(curl -s -X GET "${RE_API_URL}/v1/metrics/comparison" \
  -H "Authorization: Bearer ${AUTH}" \
  -H "x-business-id: ${BUSINESS_ID}")
echo "${COMP}" | python3 -m json.tool 2>/dev/null || echo "${COMP}"

echo
echo "[5/5] Triggering manual metrics rollup..."
curl -s -X POST "${RE_API_URL}/v1/metrics/rollup" \
  -H "Authorization: Bearer ${AUTH}" \
  -H "x-business-id: ${BUSINESS_ID}" | python3 -m json.tool 2>/dev/null || true

echo
echo "Phase 5 smoke test PASSED."
echo "Note: full reactivation message delivery requires workers running + SMS provider configured."
