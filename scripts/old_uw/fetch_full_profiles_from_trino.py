#!/usr/bin/env python3
"""
Fetch user logs and conversation snapshots for all WP-connected apps from Trino
and write data/trino_user_logs.json and data/trino_conversations.json.
Then run build_full_profiles_from_trino.py to produce data/full_profiles.json.
Run from project root. Requires direct Trino connection (TRINO_HOST in .env).

If you only have Trino MCP (no direct Trino): use MCP to run the queries in
docs/trino-query-earliest-conversation-preview.sql and the other queries,
save results to data/trino_earliest_conversation_preview.json (format: {rows, col_names:
["app_id","earliest_conversation_first_at","earliest_conversation_preview"]}),
data/trino_user_logs.json, data/trino_conversations.json, data/trino_app_metadata.json,
then run scripts/build_full_profiles_from_trino.py.
"""
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
except ImportError:
    pass

DATA = root / "data"
USER_LOGS_PATH = DATA / "trino_user_logs.json"
CONV_PATH = DATA / "trino_conversations.json"
APP_METADATA_PATH = DATA / "trino_app_metadata.json"
EARLIEST_PREVIEW_PATH = DATA / "trino_earliest_conversation_preview.json"


def _dicts_to_rows_cols(items: list, col_names: list) -> tuple[list, list]:
    """Convert list of dicts to {rows: [[...]], col_names: [...]} format."""
    rows = [[item.get(c) for c in col_names] for item in items]
    return rows, col_names


def main():
    try:
        from uw_app.trino_client import (
            is_configured,
            test_connection,
            get_all_wp_user_logs,
            get_all_wp_conversation_snapshots,
            get_all_wp_app_metadata,
            get_all_wp_earliest_conversation_preview,
            get_last_trino_error,
        )
    except ImportError as e:
        print("Error: uw_app.trino_client not available:", e)
        return 1
    if not is_configured():
        print("SKIP: Trino not configured (set TRINO_HOST in .env)")
        return 0
    if not test_connection():
        print("SKIP: Trino connection failed:", get_last_trino_error() or "(no message)")
        return 0

    logs = get_all_wp_user_logs()
    if logs is None:
        print("FAIL: get_all_wp_user_logs() failed:", get_last_trino_error() or "(no message)")
        return 1
    col_names_logs = ["app_id", "user_app_events_count", "first_activity_at", "last_activity_at"]
    rows_logs, _ = _dicts_to_rows_cols(logs, col_names_logs)
    DATA.mkdir(parents=True, exist_ok=True)
    USER_LOGS_PATH.write_text(
        json.dumps({"rows": rows_logs, "col_names": col_names_logs}, indent=2),
        encoding="utf-8",
    )
    print("Wrote", USER_LOGS_PATH, "with", len(rows_logs), "app rows.")

    conv = get_all_wp_conversation_snapshots()
    if conv is None:
        print("FAIL: get_all_wp_conversation_snapshots() failed:", get_last_trino_error() or "(no message)")
        return 1
    col_names_conv = ["app_id", "updated_date", "conversation_summary"]
    rows_conv, _ = _dicts_to_rows_cols(conv, col_names_conv)
    CONV_PATH.write_text(
        json.dumps({"rows": rows_conv, "col_names": col_names_conv}, indent=2),
        encoding="utf-8",
    )
    print("Wrote", CONV_PATH, "with", len(rows_conv), "snapshot rows.")

    meta = get_all_wp_app_metadata()
    if meta is None:
        print("Warn: get_all_wp_app_metadata() failed:", get_last_trino_error() or "(no message)")
    else:
        col_names_meta = ["app_id", "user_description", "public_settings", "categories"]
        rows_meta, _ = _dicts_to_rows_cols(meta, col_names_meta)
        APP_METADATA_PATH.write_text(
            json.dumps({"rows": rows_meta, "col_names": col_names_meta}, indent=2),
            encoding="utf-8",
        )
        print("Wrote", APP_METADATA_PATH, "with", len(rows_meta), "app metadata rows.")

    # Earliest conversation preview from base44_conversation_messages_mongo
    preview = get_all_wp_earliest_conversation_preview()
    if preview is not None:
        col_names_preview = ["app_id", "earliest_conversation_first_at", "earliest_conversation_preview"]
        rows_preview, _ = _dicts_to_rows_cols(preview, col_names_preview)
        EARLIEST_PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        EARLIEST_PREVIEW_PATH.write_text(
            json.dumps({"rows": rows_preview, "col_names": col_names_preview}, indent=2),
            encoding="utf-8",
        )
        print("Wrote", EARLIEST_PREVIEW_PATH, "with", len(rows_preview), "app preview rows.")

    # Build full_profiles.json from the data files
    build_script = root / "scripts" / "build_full_profiles_from_trino.py"
    if build_script.exists():
        r = subprocess.run([sys.executable, str(build_script)], cwd=str(root))
        if r.returncode != 0:
            print("Warn: build_full_profiles_from_trino.py exited", r.returncode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
