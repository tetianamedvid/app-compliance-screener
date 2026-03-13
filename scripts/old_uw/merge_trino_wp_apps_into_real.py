#!/usr/bin/env python3
"""
Merge Trino WP app list into data/real_apps.json.
Reads data/trino_wp_apps.json (format: {"rows": [[app_id, app_name, app_url, msid, account_id], ...], "col_names": [...]}),
merges with existing data/real_apps.json (keeps conversation_summary, linked_wp_account_id, app_context_conversation_summary),
writes back data/real_apps.json with all apps. Run from project root.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRINO_PATH = ROOT / "data" / "trino_wp_apps.json"
REAL_APPS_PATH = ROOT / "data" / "real_apps.json"
COL_NAMES = ["app_id", "app_name", "app_url", "msid", "account_id"]


def main():
    if not TRINO_PATH.exists():
        print("Missing", TRINO_PATH, "- add Trino WP app list (rows + col_names) there and re-run.")
        return 1
    trino = json.loads(TRINO_PATH.read_text(encoding="utf-8"))
    rows = trino.get("rows") or []
    cols = trino.get("col_names") or COL_NAMES
    # Existing real_apps by app_id (keep conversation_summary etc.)
    by_id = {}
    if REAL_APPS_PATH.exists():
        for r in json.loads(REAL_APPS_PATH.read_text(encoding="utf-8")):
            aid = (r.get("app_id") or "").strip()
            if aid:
                by_id[aid] = r
    out = []
    seen_app_ids = set()
    for row in rows:
        rec = dict(zip(cols, row))
        app_id = (rec.get("app_id") or "").strip()
        if not app_id or app_id in seen_app_ids:
            continue
        seen_app_ids.add(app_id)
        existing = by_id.get(app_id)
        if existing:
            # Keep existing; ensure base fields from Trino
            merged = {**existing, "app_id": app_id, "app_name": rec.get("app_name") or existing.get("app_name"), "app_url": rec.get("app_url") or existing.get("app_url"), "msid": rec.get("msid") or existing.get("msid"), "account_id": rec.get("account_id") or existing.get("account_id")}
            if merged.get("account_id"):
                merged["linked_wp_account_id"] = merged["account_id"]
            out.append(merged)
        else:
            out.append({
                "app_id": app_id,
                "app_name": rec.get("app_name") or "",
                "app_url": rec.get("app_url") or "",
                "msid": rec.get("msid") or "",
                "account_id": rec.get("account_id") or "",
                "conversation_summary": "",
                "linked_wp_account_id": rec.get("account_id") or "",
            })
    out.sort(key=lambda r: (r.get("app_id") or ""))
    REAL_APPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REAL_APPS_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Wrote", REAL_APPS_PATH, "with", len(out), "apps.")
    return 0


if __name__ == "__main__":
    exit(main())
