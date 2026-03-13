#!/usr/bin/env python3
"""
Save Trino MCP query results to data/*.json files.
Run each Trino query in Cursor (Trino MCP), copy the JSON response, paste into the
corresponding file in data/mcp_responses/ then run this script.

Setup:
  mkdir -p data/mcp_responses

For each query, create a file with the raw MCP response (the inner JSON with rows/col_names):
  - data/mcp_responses/user_logs.json
  - data/mcp_responses/conversations.json
  - data/mcp_responses/app_metadata.json
  - data/mcp_responses/earliest_preview.json

Each file should contain the MCP response text. The script extracts rows and col_names
from the response (handles both raw JSON and content-wrapped format).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MCP_DIR = DATA / "mcp_responses"
OUT = {
    "user_logs.json": DATA / "trino_user_logs.json",
    "conversations.json": DATA / "trino_conversations.json",
    "app_metadata.json": DATA / "trino_app_metadata.json",
    "earliest_preview.json": DATA / "trino_earliest_conversation_preview.json",
}
COL_NAMES = {
    "user_logs.json": ["app_id", "user_app_events_count", "first_activity_at", "last_activity_at"],
    "conversations.json": ["app_id", "updated_date", "conversation_summary"],
    "app_metadata.json": ["app_id", "user_description", "public_settings", "categories"],
    "earliest_preview.json": ["app_id", "earliest_conversation_first_at", "earliest_conversation_preview"],
}


def extract_rows_cols(raw: str):
    """Extract rows and col_names from MCP response. Handles content[].text wrapper."""
    raw = raw.strip()
    # Try direct parse
    try:
        j = json.loads(raw)
    except json.JSONDecodeError:
        # Maybe control chars - try to find the inner JSON
        match = re.search(r'\{[^{}]*"rows"\s*:\s*\[', raw)
        if match:
            start = match.start()
            depth = 0
            for i, c in enumerate(raw[start:], start):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            j = json.loads(raw[start : i + 1])
                            break
                        except Exception:
                            pass
        raise ValueError("Could not parse JSON from MCP response")
    # Unwrap content[0].text if present
    if isinstance(j, list) and j and isinstance(j[0], dict) and "content" in j[0]:
        for c in j[0]["content"]:
            if c.get("type") == "text":
                j = json.loads(c["text"])
                break
    elif isinstance(j, dict) and "content" in j:
        for c in j["content"]:
            if c.get("type") == "text":
                j = json.loads(c["text"])
                break
    rows = j.get("rows") or []
    cols = j.get("col_names") or []
    return rows, cols


def main():
    MCP_DIR.mkdir(parents=True, exist_ok=True)
    for name, out_path in OUT.items():
        src = MCP_DIR / name
        if not src.exists():
            print(f"Skip {name}: {src} not found (paste MCP response there)")
            continue
        raw = src.read_text(encoding="utf-8", errors="replace")
        try:
            rows, cols = extract_rows_cols(raw)
        except Exception as e:
            print(f"Skip {name}: parse error: {e}")
            continue
        if not cols:
            cols = COL_NAMES.get(name, [])
        # Ensure rows are JSON-serializable (timestamps as strings)
        for row in rows:
            for i in range(len(row)):
                v = row[i]
                if v is not None and hasattr(v, "strftime"):
                    row[i] = v.strftime("%Y-%m-%d %H:%M:%S")
                elif v is not None and not isinstance(v, (str, int, float, bool, list, dict)):
                    row[i] = str(v)[:50]
        out = {"rows": rows, "col_names": cols}
        out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"Wrote {out_path.name} with {len(rows)} rows")
    print("\nNow run: python3 scripts/build_full_profiles_from_trino.py")


if __name__ == "__main__":
    main()
