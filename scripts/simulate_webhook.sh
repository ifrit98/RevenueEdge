#!/usr/bin/env bash
#
# Simulate Retell webhook events for end-to-end testing.
#
# Requires: ENVIRONMENT=test in .env (skips signature verification)
#
# Usage:
#   ./scripts/simulate_webhook.sh missed-call <business_id>
#   ./scripts/simulate_webhook.sh sms <business_id> "I need a plumber ASAP"
#   ./scripts/simulate_webhook.sh call-ended <business_id>
#   ./scripts/simulate_webhook.sh call-started <business_id>
#
# You can also read business_id from test_account.json:
#   ./scripts/simulate_webhook.sh missed-call auto

set -euo pipefail

WEBHOOKS_URL="${RE_WEBHOOKS_URL:-http://localhost:8081}"
ENDPOINT="${WEBHOOKS_URL}/webhooks/retell"
DID="${TEST_DID:-+15555550123}"
CALLER="${TEST_CALLER:-+12025551234}"

resolve_business_id() {
    local arg="$1"
    if [[ "$arg" == "auto" ]]; then
        if [[ -f "test_account.json" ]]; then
            python3 -c "import json; print(json.load(open('test_account.json'))['business_id'])"
        else
            echo "ERROR: test_account.json not found. Run setup_test_account.py first or pass a business_id." >&2
            exit 1
        fi
    else
        echo "$arg"
    fi
}

rand_call_id() {
    python3 -c "import uuid; print(str(uuid.uuid4()))"
}

timestamp_ms() {
    python3 -c "import time; print(int(time.time() * 1000))"
}

send_webhook() {
    local scenario="$1"
    local payload="$2"

    echo "=== Simulating: $scenario ==="
    echo "  Endpoint: $ENDPOINT"
    echo "  DID:      $DID"
    echo "  Caller:   $CALLER"
    echo ""

    response=$(curl -s -w "\n%{http_code}" -X POST "$ENDPOINT" \
        -H "Content-Type: application/json" \
        -d "$payload")

    http_code=$(echo "$response" | tail -1)
    body=$(echo "$response" | sed '$d')

    echo "  HTTP $http_code"
    echo "  Response: $body"
    echo ""

    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        echo "  OK"
    else
        echo "  FAILED — check webhooks service logs"
    fi
    echo ""
}

# ── Commands ─────────────────────────────────────────────────────────

cmd_missed_call() {
    local biz_id="$1"
    local call_id
    call_id=$(rand_call_id)
    local ts
    ts=$(timestamp_ms)

    send_webhook "Missed Call" "$(cat <<JSON
{
  "event": "call_ended",
  "call": {
    "call_id": "$call_id",
    "from_number": "$CALLER",
    "to_number": "$DID",
    "call_status": "ended",
    "call_type": "phone_call",
    "direction": "inbound",
    "disconnection_reason": "no_answer",
    "start_timestamp": $ts,
    "end_timestamp": $((ts + 2000)),
    "duration_ms": 2000,
    "transcript": null,
    "recording_url": null,
    "agent_id": "test-agent-001",
    "metadata": {
      "trace_id": "sim-missed-$call_id",
      "business_id": "$biz_id"
    }
  }
}
JSON
)"
}

cmd_sms() {
    local biz_id="$1"
    local message="${2:-Hi, I need help with a plumbing issue.}"
    local call_id
    call_id=$(rand_call_id)
    local ts
    ts=$(timestamp_ms)

    send_webhook "Inbound SMS" "$(cat <<JSON
{
  "event": "chat_message_created",
  "call": {
    "call_id": "$call_id",
    "from_number": "$CALLER",
    "to_number": "$DID",
    "call_status": "ended",
    "call_type": "chat",
    "direction": "chat",
    "start_timestamp": $ts,
    "end_timestamp": $ts,
    "duration_ms": 0,
    "transcript": "$message",
    "agent_id": "test-agent-001",
    "metadata": {
      "trace_id": "sim-sms-$call_id",
      "business_id": "$biz_id"
    }
  }
}
JSON
)"
}

cmd_call_ended() {
    local biz_id="$1"
    local call_id
    call_id=$(rand_call_id)
    local ts
    ts=$(timestamp_ms)

    send_webhook "Completed Call" "$(cat <<JSON
{
  "event": "call_ended",
  "call": {
    "call_id": "$call_id",
    "from_number": "$CALLER",
    "to_number": "$DID",
    "call_status": "ended",
    "call_type": "phone_call",
    "direction": "inbound",
    "disconnection_reason": "agent_hangup",
    "start_timestamp": $ts,
    "end_timestamp": $((ts + 180000)),
    "duration_ms": 180000,
    "transcript": "Customer: Hi, my kitchen sink is leaking and I need someone to come take a look. Agent: I'd be happy to help with that. Can I get your name and address? Customer: Sure, I'm John Smith at 123 Oak Street. Agent: Great, I can schedule a technician. Would tomorrow morning between 9 and 11 work? Customer: That works perfectly. Agent: I've got you scheduled. The technician will call 30 minutes before arriving. Anything else? Customer: No, that's it. Thank you! Agent: You're welcome, we'll see you tomorrow!",
    "recording_url": "https://example.com/recordings/test.mp3",
    "agent_id": "test-agent-001",
    "call_analysis": {
      "call_summary": "Customer called about a leaking kitchen sink. Appointment scheduled for tomorrow morning.",
      "custom_analysis_data": {
        "customer_name": "John Smith",
        "address": "123 Oak Street",
        "issue": "leaking kitchen sink",
        "intent": "book_service"
      }
    },
    "metadata": {
      "trace_id": "sim-call-$call_id",
      "business_id": "$biz_id"
    }
  }
}
JSON
)"
}

cmd_call_started() {
    local biz_id="$1"
    local call_id
    call_id=$(rand_call_id)
    local ts
    ts=$(timestamp_ms)

    send_webhook "Call Started" "$(cat <<JSON
{
  "event": "call_started",
  "call": {
    "call_id": "$call_id",
    "from_number": "$CALLER",
    "to_number": "$DID",
    "call_status": "ongoing",
    "call_type": "phone_call",
    "direction": "inbound",
    "start_timestamp": $ts,
    "agent_id": "test-agent-001",
    "metadata": {
      "trace_id": "sim-start-$call_id",
      "business_id": "$biz_id"
    }
  }
}
JSON
)"
}

cmd_full_scenario() {
    local biz_id="$1"
    echo "Running full scenario: missed call → SMS inquiry → completed call"
    echo "================================================================"
    echo ""

    echo "[1/4] Missed call..."
    cmd_missed_call "$biz_id"
    echo "Waiting 3s for workers to process..."
    sleep 3

    echo "[2/4] Customer texts back..."
    cmd_sms "$biz_id" "I just tried calling about a plumbing emergency. My pipe burst!"
    echo "Waiting 3s for workers to process..."
    sleep 3

    echo "[3/4] Customer calls back, gets through..."
    cmd_call_started "$biz_id"
    sleep 1

    echo "[4/4] Call ends successfully with booking..."
    cmd_call_ended "$biz_id"

    echo ""
    echo "Full scenario complete. Check:"
    echo "  - Dashboard conversations page for the new conversation"
    echo "  - Dashboard leads page for the qualified lead"
    echo "  - Worker logs for processing details"
}

# ── Main ─────────────────────────────────────────────────────────────

usage() {
    cat <<USAGE
Usage: $0 <command> <business_id> [args...]

Commands:
  missed-call   <business_id>              Simulate a missed/unanswered call
  sms           <business_id> [message]    Simulate an inbound SMS
  call-ended    <business_id>              Simulate a completed call with transcript
  call-started  <business_id>              Simulate a call-started event
  full          <business_id>              Run a full multi-step scenario

Pass 'auto' as business_id to read from test_account.json.

Environment:
  RE_WEBHOOKS_URL   Webhook endpoint (default: http://localhost:8081)
  TEST_DID          The business DID (default: +15555550123)
  TEST_CALLER       The customer phone (default: +12025551234)

USAGE
    exit 1
}

if [[ $# -lt 2 ]]; then
    usage
fi

COMMAND="$1"
BIZ_ID=$(resolve_business_id "$2")
shift 2

case "$COMMAND" in
    missed-call)  cmd_missed_call "$BIZ_ID" ;;
    sms)          cmd_sms "$BIZ_ID" "${1:-}" ;;
    call-ended)   cmd_call_ended "$BIZ_ID" ;;
    call-started) cmd_call_started "$BIZ_ID" ;;
    full)         cmd_full_scenario "$BIZ_ID" ;;
    *)            echo "Unknown command: $COMMAND"; usage ;;
esac
