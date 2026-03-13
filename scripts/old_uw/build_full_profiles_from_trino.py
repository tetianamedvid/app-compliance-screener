#!/usr/bin/env python3
"""
Build data/full_profiles.json from Trino MCP query results.
Reads data/trino_conversations.json, data/trino_user_logs.json, data/trino_app_metadata.json,
data/trino_earliest_conversation_preview.json (format: {rows, col_names}),
outputs data/full_profiles.json keyed by app_id for the dashboard.
Run from project root.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONV_PATH = DATA / "trino_conversations.json"
LOGS_PATH = DATA / "trino_user_logs.json"
APP_METADATA_PATH = DATA / "trino_app_metadata.json"
EARLIEST_PREVIEW_PATH = DATA / "trino_earliest_conversation_preview.json"
OUT_PATH = DATA / "full_profiles.json"


def _rows_to_list_of_dicts(rows: list, col_names: list) -> list[dict]:
    return [dict(zip(col_names, row)) for row in rows]


def main():
    full = {}
    if LOGS_PATH.exists():
        try:
            data = json.loads(LOGS_PATH.read_text(encoding="utf-8"))
            rows = data.get("rows") or []
            cols = data.get("col_names") or []
            for d in _rows_to_list_of_dicts(rows, cols):
                aid = (d.get("app_id") or "").strip()
                if not aid:
                    continue
                if aid not in full:
                    full[aid] = {}
                full[aid]["user_app_events_count"] = d.get("user_app_events_count")
                full[aid]["first_activity_at"] = d.get("first_activity_at")
                full[aid]["user_apps_last_activity_at"] = d.get("last_activity_at")
        except Exception as e:
            print("Warn: could not load user logs:", e)
    if CONV_PATH.exists():
        try:
            data = json.loads(CONV_PATH.read_text(encoding="utf-8"))
            rows = data.get("rows") or []
            cols = data.get("col_names") or []
            for d in _rows_to_list_of_dicts(rows, cols):
                aid = (d.get("app_id") or "").strip()
                content = (d.get("conversation_summary") or "").strip()
                if not aid or not content:
                    continue
                if aid not in full:
                    full[aid] = {}
                created_at = d.get("updated_date")
                if created_at is not None:
                    try:
                        created_at = str(created_at)[:19].replace("T", " ")
                    except Exception:
                        created_at = "—"
                else:
                    created_at = "—"
                full[aid].setdefault("conversation_snapshots", []).append({
                    "created_at": created_at,
                    "content": content,
                })
        except Exception as e:
            print("Warn: could not load conversations:", e)
    if APP_METADATA_PATH.exists():
        try:
            data = json.loads(APP_METADATA_PATH.read_text(encoding="utf-8"))
            rows = data.get("rows") or []
            cols = data.get("col_names") or []
            for d in _rows_to_list_of_dicts(rows, cols):
                aid = (d.get("app_id") or "").strip()
                if not aid:
                    continue
                if aid not in full:
                    full[aid] = {}
                full[aid]["user_description"] = d.get("user_description")
                full[aid]["public_settings"] = d.get("public_settings")
                full[aid]["categories"] = d.get("categories")
        except Exception as e:
            print("Warn: could not load app metadata:", e)
    if EARLIEST_PREVIEW_PATH.exists():
        try:
            data = json.loads(EARLIEST_PREVIEW_PATH.read_text(encoding="utf-8"))
            rows = data.get("rows") or []
            cols = data.get("col_names") or []
            for d in _rows_to_list_of_dicts(rows, cols):
                aid = (d.get("app_id") or "").strip()
                preview = (d.get("earliest_conversation_preview") or "").strip()
                if not aid or not preview:
                    continue
                if aid not in full:
                    full[aid] = {}
                full[aid]["earliest_conversation_preview"] = preview
                ts = d.get("earliest_conversation_first_at")
                if ts is not None:
                    try:
                        full[aid]["earliest_conversation_first_at"] = str(ts)[:19].replace("T", " ")
                    except Exception:
                        full[aid]["earliest_conversation_first_at"] = str(ts)
                else:
                    full[aid]["earliest_conversation_first_at"] = None
        except Exception as e:
            print("Warn: could not load earliest conversation preview:", e)
    DATA.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(full, indent=2), encoding="utf-8")
    print("Wrote", OUT_PATH, "with", len(full), "apps.")


if __name__ == "__main__":
    main()
