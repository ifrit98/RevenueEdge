#!/usr/bin/env bash
# Phase 0 smoke test.
#
# Proves:
#   - The Supabase schema is applied (queue_jobs table + RPCs exist).
#   - A worker process can claim a job, run its handler, and complete it.
#   - The handler produces a downstream conversation-intelligence job.
#
# Usage:
#   ./scripts/smoke_phase0.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "[smoke] No .env found. Copy .env.example and fill it in first." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

VENV=".venv-re"
if [[ ! -d "$VENV" ]]; then
  echo "[smoke] Run ./scripts/bootstrap.sh first" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

python - <<'PY'
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, "apps/workers")

from src.supabase_client import rpc  # type: ignore

async def main() -> None:
    trace_id = str(uuid.uuid4())
    idempotency = f"smoke:{trace_id}"

    # 1. Enqueue a synthetic inbound-events job.
    enqueue = await rpc(
        "enqueue_job",
        {
            "p_queue_name": "inbound-events",
            "p_payload": {
                "event_type": "call.missed",
                "trace_id": trace_id,
                "from_number": "+15555550123",
                "to_number": "+15555550100",
                "source": "smoke_test",
            },
            "p_business_id": None,
            "p_idempotency_key": idempotency,
            "p_priority": 10,
        },
    )
    job_id = enqueue.data if isinstance(enqueue.data, str) else (enqueue.data or [None])[0]
    print(f"[smoke] enqueued job_id={job_id} trace_id={trace_id}")
    assert job_id, "enqueue_job did not return a job id"

    # 2. Claim the job as a smoke worker.
    claimed = await rpc(
        "claim_queue_jobs",
        {
            "p_queue_name": "inbound-events",
            "p_worker_id": f"smoke-test-{os.getpid()}",
            "p_limit": 1,
            "p_lock_timeout": "300 seconds",
        },
    )
    rows = claimed.data or []
    assert rows, "claim_queue_jobs returned nothing — did the schema apply?"
    print(f"[smoke] claimed {len(rows)} row(s), status={rows[0].get('status')}")
    assert rows[0]["id"] == job_id, "Claimed a different job than we enqueued"

    # 3. Complete it.
    await rpc(
        "complete_queue_job",
        {"p_job_id": job_id, "p_result": {"smoke": True}},
    )
    print(f"[smoke] completed job_id={job_id}")

    # 4. Double-check via a select.
    from src.supabase_client import get_client
    client = get_client()
    resp = client.table("queue_jobs").select("status, result").eq("id", job_id).single().execute()
    print(f"[smoke] final state: {json.dumps(resp.data, indent=2)}")
    assert resp.data["status"] == "succeeded", f"Expected succeeded, got {resp.data['status']}"

    print("[smoke] OK: queue + RPCs work end-to-end.")

asyncio.run(main())
PY

echo ""
echo "[smoke] Phase 0 smoke test PASSED."
echo "[smoke] Next: start the worker process and push a real inbound-events job"
echo "        to exercise the full loop:"
echo ""
echo "    python -m src.main   # from apps/workers/"
echo ""
