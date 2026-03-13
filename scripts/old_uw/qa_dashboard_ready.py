#!/usr/bin/env python3
"""
Quick check that the UW Lookup dashboard has everything it needs to run.
Run from project root:  python3 scripts/qa_dashboard_ready.py
Exits 0 if app list loads, at least one app resolves and has a profile (so the UI would work).
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

from uw_app.resolve import load_apps_index_merged, get_apps_json_path
from uw_app.profile import profile_from_app_record


def main():
    # 1. App list
    by_app_id, _, _, _ = load_apps_index_merged()
    if not by_app_id:
        print("FAIL: No apps in list. Set APPS_JSON_PATH to data/real_apps.json (or run merge_trino_wp_apps_into_real.py).")
        return 1
    apps_path = get_apps_json_path()
    print("OK: App list has", len(by_app_id), "apps from", apps_path.name)

    # 2. One app resolves and has profile
    first_id = next(iter(by_app_id))
    record = by_app_id[first_id]
    profile_rows = profile_from_app_record(record)
    if len(profile_rows) < 3:
        print("FAIL: First app has too few profile rows (need app_id, app_name, app_url, etc.)")
        return 1
    print("OK: Sample app", first_id, "has", len(profile_rows), "profile rows")

    # 3. Optional full_profiles path (dashboard uses it when Trino is off)
    full_profiles_path = root / "data" / "full_profiles.json"
    if full_profiles_path.exists():
        print("OK: full_profiles.json exists (used when Trino unavailable)")
    else:
        print("Note: data/full_profiles.json missing (optional; run fetch_full_profiles_from_trino.py when Trino is available)")

    print("Dashboard is ready to QA. Run: python3 run_uw_app.py  then open http://localhost:8501")
    return 0


if __name__ == "__main__":
    sys.exit(main())
