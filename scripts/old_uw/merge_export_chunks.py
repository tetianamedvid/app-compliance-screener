#!/usr/bin/env python3
"""
Merge multiple chunked export files (CSV or JSON) from the full-app-profile Trino query
into one JSON for the UW Lookup app. Use when Quix says "too big": run the chunked query
multiple times (OFFSET 0, 500, 1000, ...), export each to a file, then run this script.

Usage:
  python3 scripts/merge_export_chunks.py chunk_0.csv chunk_1.csv chunk_2.csv --out data/real_apps.json
  python3 scripts/merge_export_chunks.py chunk_0.json chunk_1.json --out data/real_apps.json

Full columns are kept (no truncation). Duplicate app_id (same app in multiple chunks) keeps the last row.
"""
import argparse
import json
import sys
from pathlib import Path

# So "from import_apps_export import ..." works when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_apps_export import load_csv, load_json


def main():
    parser = argparse.ArgumentParser(
        description="Merge chunked Trino/Quix export files into one JSON for the UW Lookup app."
    )
    parser.add_argument(
        "input",
        nargs="+",
        type=Path,
        help="Paths to chunk files (CSV or JSON)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: data/real_apps.json)",
    )
    args = parser.parse_args()
    out = args.out or (Path(__file__).resolve().parent.parent / "data" / "real_apps.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    for path in args.input:
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        if path.suffix.lower() == ".csv":
            rows = load_csv(path)
        else:
            rows = load_json(path)
        all_rows.extend(rows)

    # One row per app_id; later chunks overwrite earlier (order: chunk0, chunk1, ...)
    by_app_id = {}
    for r in all_rows:
        aid = r.get("app_id")
        if aid:
            by_app_id[aid] = r

    merged = list(by_app_id.values())
    merged.sort(key=lambda r: (r.get("app_id") or ""))

    if not merged:
        print("Error: no rows with app_id in any chunk.", file=sys.stderr)
        sys.exit(1)

    out.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"Merged {len(merged)} apps from {len(args.input)} chunk(s) -> {out}")
    print("Next: in .env set  APPS_JSON_PATH=data/real_apps.json  (or the path you used with --out)")
    print("Then restart the app.")


if __name__ == "__main__":
    main()
