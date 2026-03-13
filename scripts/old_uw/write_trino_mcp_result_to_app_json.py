#!/usr/bin/env python3
"""
Read Trino MCP tool output file (JSON with "content"[0]["text"] containing "rows" array)
and write normalized app list to data/real_apps.json for the UW Lookup app.
Usage: python3 scripts/write_trino_mcp_result_to_app_json.py <path_to_mcp_output.txt>
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = PROJECT_ROOT / "data" / "real_apps.json"
COLUMNS = ["app_id", "app_name", "app_url", "msid", "linked_wp_account_id", "app_context_conversation_summary"]


def main():
    if len(sys.argv) < 2:
        print("Usage: write_trino_mcp_result_to_app_json.py <path_to_mcp_output.txt>", file=sys.stderr)
        sys.exit(1)
    inp = Path(sys.argv[1])
    if not inp.exists():
        print(f"File not found: {inp}", file=sys.stderr)
        sys.exit(1)
    raw = json.loads(inp.read_text(encoding="utf-8"))
    text = raw.get("content", [{}])[0].get("text", "")
    # MCP may return large output with unescaped newlines in strings; try parse then fallback
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Replace raw newlines inside quoted strings so parse succeeds (crude but often works)
        import re
        text_clean = re.sub(r'(?<!\\)"(?:[^"\\]|\\.)*', lambda m: m.group(0).replace("\n", " ").replace("\r", " "), text)
        try:
            data = json.loads(text_clean)
        except json.JSONDecodeError:
            print("Could not parse MCP output as JSON (file may be truncated or contain invalid chars). Export from Quix as CSV/JSON and use import_apps_export.py instead.", file=sys.stderr)
            sys.exit(1)
    rows = data.get("rows", [])
    out = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < len(COLUMNS):
            continue
        row = dict(zip(COLUMNS, r))
        app_id = row.get("app_id")
        if not app_id:
            continue
        normalized = {
            "app_id": str(app_id),
            "app_name": (row.get("app_name") or "").strip() or None,
            "app_url": (row.get("app_url") or "").strip() or None,
            "msid": (row.get("msid") or "").strip() or None,
            "account_id": (row.get("linked_wp_account_id") or "").strip() or None,
            "conversation_summary": (row.get("app_context_conversation_summary") or "").strip() or "",
        }
        for k, v in row.items():
            if k not in normalized and v is not None and str(v).strip():
                normalized[k] = v.strip() if isinstance(v, str) else v
        out.append(normalized)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {len(out)} apps to {OUT_PATH}")


if __name__ == "__main__":
    main()
