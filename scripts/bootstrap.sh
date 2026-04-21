#!/usr/bin/env bash
# Phase 0 bootstrap. One-shot local setup.
#
# Prereqs:
#   - Python 3.11+
#   - A Supabase project (hosted or local via `supabase` CLI)
#   - .env filled in from .env.example
#
# What this does:
#   1. Creates a shared virtualenv at .venv-re/
#   2. Installs deps for api, webhooks, workers
#   3. Applies schema.sql + seed_mvp_defaults.sql to the configured Supabase
#      project. If SUPABASE_DB_URL is set, uses psql; otherwise prints the
#      SQL files for manual copy-paste into the Supabase SQL editor.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "[bootstrap] No .env found. Copy .env.example and fill it in first." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

VENV=".venv-re"
if [[ ! -d "$VENV" ]]; then
  echo "[bootstrap] Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --upgrade pip >/dev/null

echo "[bootstrap] Installing deps (api)"
pip install -r apps/api/requirements.txt >/dev/null

echo "[bootstrap] Installing deps (webhooks)"
pip install -r apps/webhooks/requirements.txt >/dev/null

echo "[bootstrap] Installing deps (workers)"
pip install -r apps/workers/requirements.txt >/dev/null

echo "[bootstrap] Python deps installed."

if [[ -n "${SUPABASE_DB_URL:-}" ]]; then
  echo "[bootstrap] Applying schema.sql"
  psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/schema.sql
  echo "[bootstrap] Applying seed_mvp_defaults.sql"
  psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/seed_mvp_defaults.sql
  echo "[bootstrap] DB migrated."
else
  cat <<EOF
[bootstrap] SUPABASE_DB_URL not set.

Option A — apply via Supabase SQL Editor:
  1. Open the Supabase project SQL editor.
  2. Paste supabase/schema.sql and Run.
  3. Paste supabase/seed_mvp_defaults.sql and Run.

Option B — install the Supabase CLI + re-run with SUPABASE_DB_URL set:
  export SUPABASE_DB_URL="postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres"
  ./scripts/bootstrap.sh
EOF
fi

echo "[bootstrap] Done. Next: ./scripts/smoke_phase0.sh"
