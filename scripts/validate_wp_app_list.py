#!/usr/bin/env python3
"""
Validation: compare local app list size to Trino distinct count of WP-connected app_ids.
Data are considered full when local count equals Trino count.
Run from project root:  python3 scripts/validate_wp_app_list.py
Exits 0 if full or Trino skipped; 1 if counts differ.
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
except ImportError:
    pass

from uw_app.resolve import load_apps_index_merged


def main():
    by_app_id, _, _ = load_apps_index_merged()
    local_count = len(by_app_id)

    try:
        from uw_app.trino_client import is_configured, test_connection, get_wp_connected_app_count
    except ImportError:
        print("SKIP: Trino client not available — cannot validate WP app count")
        return 0
    if not is_configured():
        print("SKIP: Trino not configured (TRINO_HOST) — cannot validate WP app count")
        return 0
    if not test_connection():
        print("SKIP: Trino connection failed — cannot validate WP app count")
        return 0

    trino_count = get_wp_connected_app_count()
    if trino_count is None:
        print("SKIP: Trino WP-connected app count query failed — cannot validate")
        return 0

    if local_count == trino_count:
        print("OK: data full —", local_count, "apps match Trino WP-connected count")
        return 0
    print(
        "FAIL: local app list has",
        local_count,
        "apps; Trino distinct WP-connected count is",
        trino_count,
        "— data not full. Run scripts/merge_trino_wp_apps_into_real.py after updating data/trino_wp_apps.json.",
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
