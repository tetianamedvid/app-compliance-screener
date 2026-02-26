#!/usr/bin/env python3
"""
QA script for UW Lookup: resolve, basic profile, and (when Trino works) full profile + conversation.
Run from project root:  python3 scripts/qa_uw_lookup.py
Exits 0 if resolve + profile pass; when Trino is live, also checks full profile and conversation path.
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
except ImportError:
    pass

from uw_app.resolve import resolve, load_apps_index_merged
from uw_app.profile import profile_from_app_record

TEST_APP_ID = "6914aa3bd56f6388c0b30a84"  # QUIKPICS in real_apps.json

# Extended fields from Trino full-profile (profile skips conversation_summary)
EXTENDED_FIELDS = ("first_activity_at", "user_app_events_count", "user_description", "categories", "public_settings")


def main():
    by_app_id, _, _ = load_apps_index_merged()
    if TEST_APP_ID not in by_app_id:
        print(f"FAIL: {TEST_APP_ID} not in merged index (check APPS_JSON_PATH and data/real_apps.json)")
        return 1
    row = by_app_id[TEST_APP_ID]
    if not (row.get("app_name") and row.get("app_url")):
        print("FAIL: merged record has empty app_name or app_url (user_apps may be overwriting; check merge order)")
        return 1
    app_record = resolve("app_id", TEST_APP_ID)
    if not app_record:
        print("FAIL: resolve() returned None")
        return 1
    if not app_record.get("app_name") or not app_record.get("app_url"):
        print("FAIL: resolve() returned record with empty app_name or app_url")
        return 1
    profile_rows = profile_from_app_record(app_record)
    if len(profile_rows) < 4:
        print(f"FAIL: profile has only {len(profile_rows)} rows (expected 4+)")
        return 1
    fields = {r["field"] for r in profile_rows}
    for required in ("app_id", "app_name", "app_url"):
        if required not in fields:
            print(f"FAIL: profile missing field '{required}'")
            return 1
    print("OK: resolve + basic profile for", TEST_APP_ID, "(", app_record.get("app_name"), ")")

    # Trino full profile when configured and live
    try:
        from uw_app.trino_client import (
            trino_configured,
            test_connection,
            get_full_profile,
            get_conversation_snapshots,
            get_conversation_messages,
            get_last_trino_error,
        )
    except ImportError:
        print("SKIP: Trino client not available")
        return 0
    if not trino_configured():
        print("SKIP: Trino not configured")
        return 0
    if not test_connection():
        print("SKIP: Trino test_connection() failed (check host/cookie)")
        return 0
    profile_row = get_full_profile(TEST_APP_ID)
    if profile_row is None:
        err = get_last_trino_error()
        print("FAIL: Trino full profile failed; fix connection/cookie. Last error:", err or "(none)")
        return 1
    has_extended = any(profile_row.get(f) for f in EXTENDED_FIELDS)
    if not has_extended:
        print("FAIL: full profile returned but no extended field (first_activity_at, user_app_events_count, user_description, categories, public_settings)")
        return 1
    print("OK: Trino full profile has extended fields")
    snapshots = get_conversation_snapshots(TEST_APP_ID)
    print("OK: conversation snapshots count:", len(snapshots))
    messages = get_conversation_messages(TEST_APP_ID)
    print("OK: conversation messages count:", len(messages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
