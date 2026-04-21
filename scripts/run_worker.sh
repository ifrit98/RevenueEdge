#!/usr/bin/env bash
# Run the worker process locally.
#
# Usage:
#   ./scripts/run_worker.sh                        # all MVP workers
#   WORKERS=inbound_normalizer ./scripts/run_worker.sh   # single worker

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "[run-worker] No .env found" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

VENV=".venv-re"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

cd apps/workers
exec python -m src.main
