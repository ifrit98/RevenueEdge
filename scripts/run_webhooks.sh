#!/usr/bin/env bash
# Run the re-webhooks service locally.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "[run-webhooks] No .env found" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

VENV=".venv-re"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

cd apps/webhooks
exec uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload
