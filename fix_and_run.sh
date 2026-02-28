#!/bin/bash
# Run full pipeline and launch dashboard. Use local data (TRINO_USE_LOCAL_ONLY=1).
# From project root: ./fix_and_run.sh
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "1. Building full_profiles.json from Trino export files (if any)..."
python3 scripts/build_full_profiles_from_trino.py 2>/dev/null || true

echo "2. Running UW for apps missing conclusions..."
python3 scripts/run_uw_for_missing.py --run-id uw_lookup --llm none 2>/dev/null || true

echo "3. Starting dashboard..."
python3 run_uw_app.py
