#!/usr/bin/env python3
"""
Import a real data extract (CSV or JSON) into the format the UW Lookup app uses.
Use this instead of the 7 QA samples when you have a real export from Quix, Trino, or your DB.

Usage:
  python3 scripts/import_apps_export.py path/to/your_export.csv
  python3 scripts/import_apps_export.py path/to/your_export.json
  python3 scripts/import_apps_export.py path/to/export.csv --out data/real_apps.json

Then in .env set:  APPS_JSON_PATH=data/real_apps.json
Restart the app. You get full profiles for all apps in the export.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

# Column name variants -> canonical key for the app list format
COLUMN_MAP = {
    "app_id": "app_id",
    "appid": "app_id",
    "app id": "app_id",
    "id": "app_id",
    "app_name": "app_name",
    "appname": "app_name",
    "name": "app_name",
    "app name": "app_name",
    "app_url": "app_url",
    "appurl": "app_url",
    "url": "app_url",
    "app url": "app_url",
    "msid": "msid",
    "account_id": "account_id",
    "accountid": "account_id",
    "wp_account_id": "account_id",
    "conversation_summary": "conversation_summary",
    "conversationsummary": "conversation_summary",
    "summary": "conversation_summary",
    "app_context_conversation_summary": "conversation_summary",
    "linked_wp_account_id": "account_id",
}


def normalize_row(raw: dict) -> dict:
    """Map raw columns to canonical keys; keep any extra columns for full profile."""
    out = {}
    keys_lower = {k.strip().lower(): k for k in raw.keys()}
    for variant, canonical in COLUMN_MAP.items():
        if variant in keys_lower:
            orig_key = keys_lower[variant]
            val = raw.get(orig_key)
            if val is not None and str(val).strip() != "":
                out[canonical] = val if not isinstance(val, str) else val.strip()
    # Keep all original keys so full export = full profile (entity_id, base44_email, etc.)
    for k, v in raw.items():
        if k not in out and v is not None and str(v).strip() != "":
            out[k] = v if not isinstance(v, str) else v.strip()
    return out


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [normalize_row(row) for row in reader]


def load_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [normalize_row(r) if isinstance(r, dict) else {} for r in data]
    if isinstance(data, dict):
        return [normalize_row(data)]
    return []


def main():
    parser = argparse.ArgumentParser(description="Import real data extract for UW Lookup app.")
    parser.add_argument("input", type=Path, help="Path to export file (CSV or JSON)")
    parser.add_argument("--out", type=Path, default=None, help="Output JSON path (default: data/real_apps.json)")
    args = parser.parse_args()
    inp = args.input
    if not inp.exists():
        print(f"Error: file not found: {inp}", file=sys.stderr)
        sys.exit(1)
    out = args.out or (Path(__file__).resolve().parent.parent / "data" / "real_apps.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    if inp.suffix.lower() == ".csv":
        rows = load_csv(inp)
    else:
        rows = load_json(inp)

    # Keep only rows that have at least app_id
    rows = [r for r in rows if r.get("app_id")]
    if not rows:
        print("Error: no rows with app_id found in the export.", file=sys.stderr)
        sys.exit(1)

    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} apps to {out}")
    print("Next: in .env set  APPS_JSON_PATH=data/real_apps.json  (or the path you used with --out)")
    print("Then restart the app. You will get full profiles for these apps.")


if __name__ == "__main__":
    main()
