#!/usr/bin/env bash
# Remedial run: fetch from Trino, run UW for missing apps, start dashboard.
# Run from project root. Requires Trino configured in .env.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "Step 1/3: Fetching profile and conversation data from Trino..."
python3 scripts/fetch_full_profiles_from_trino.py
if [ $? -ne 0 ]; then
  echo "Warning: Trino fetch had non-zero exit. Continuing."
fi

echo "Step 2/3: Running underwriting for apps missing conclusions..."
python3 scripts/run_uw_for_missing.py
if [ $? -ne 0 ]; then
  echo "Warning: UW for missing had non-zero exit. Continuing."
fi

echo "Step 3/3: Starting dashboard (sync already done above)..."
SKIP_STARTUP_SYNC=1 exec python3 run_uw_app.py
