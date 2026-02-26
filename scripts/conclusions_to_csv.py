#!/usr/bin/env python3
"""Parse underwriting conclusion .md files and write one CSV row per app.
Usage: python conclusions_to_csv.py [output_dir] [--apps path/to/apps.json]
  If --apps is given, raw_conversation_summary (and raw_app_content if missing in file) are filled from the JSON for each app_id.
"""
import csv
import json
import re
import sys
from pathlib import Path

def parse_conclusion(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    row = {}
    # Header
    m = re.search(r"# Underwriting conclusion — (.+?)\n", text)
    row["app_id"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*App name:\*\* (.+?)\n", text)
    row["app_name"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*App URL:\*\* (.+?)\n", text)
    row["app_url"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Date:\*\* (.+?)\n", text)
    row["date"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Sources checked:\*\* (.+?)\n", text)
    row["sources_checked"] = m.group(1).strip() if m else ""

    # Raw evidence (if present)
    raw_sec = re.search(r"## Raw evidence\s*\n\*\*Conversation summary \(raw\):\*\*\s*\n(.*?)(?=\n\*\*App description|\Z)", text, re.DOTALL)
    row["raw_conversation_summary"] = raw_sec.group(1).strip() if raw_sec else ""
    raw_app = re.search(r"\*\*App description / scraped content \(raw\):\*\*\s*\n(.*?)(?=\n---\n\n## App summary|\Z)", text, re.DOTALL)
    row["raw_app_content"] = raw_app.group(1).strip() if raw_app else ""

    # App summary section (between ## App summary and ## Policy comparison)
    app_sec = re.search(r"## App summary \(middleman\)\s*\n(.*?)(?=\n## Policy comparison|\Z)", text, re.DOTALL)
    row["app_summary"] = app_sec.group(1).strip() if app_sec else ""

    # Policy section
    pol_sec = re.search(r"## Policy comparison and verdict\s*\n(.*)", text, re.DOTALL)
    pol_text = pol_sec.group(1).strip() if pol_sec else ""

    m = re.search(r"\*\*Step 1 — What is sold:\*\*\s*\n(.*?)(?=\n\*\*Step 2|\Z)", pol_text, re.DOTALL)
    row["step1_what_sold"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Step 2 — Comparison to policy:\*\*\s*\n(.*?)(?=\n\*\*Step 3|\Z)", pol_text, re.DOTALL)
    row["step2_comparison"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Step 3 — Verdict:\*\* (.+?)(?=\s*\n\*\*Reasoning|\Z)", pol_text, re.DOTALL)
    row["verdict"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Reasoning:\*\*\s*(.+?)(?=\n\*\*Non-compliant|\Z)", pol_text, re.DOTALL)
    row["reasoning"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Non-compliant subcategories:\*\*\s*(.+)", pol_text, re.DOTALL)
    row["non_compliant_subcategories"] = m.group(1).strip() if m else ""

    return row


def main():
    args = [a for a in sys.argv[1:] if a != "--apps"]
    apps_path = None
    if "--apps" in sys.argv:
        i = sys.argv.index("--apps")
        if i + 1 < len(sys.argv):
            apps_path = Path(sys.argv[i + 1])
    out_dir = Path(__file__).resolve().parent.parent / "output" / "run_two_step_7apps_v3"
    if args:
        out_dir = Path(args[0])
    paths = sorted(out_dir.glob("conclusion_*.md"))
    if not paths:
        print(f"No conclusion_*.md in {out_dir}", file=sys.stderr)
        sys.exit(1)
    rows = [parse_conclusion(p) for p in paths]

    # Optionally fill raw_conversation_summary (and raw_app_content) from apps JSON
    if apps_path and apps_path.exists():
        data = json.loads(apps_path.read_text(encoding="utf-8"))
        by_id = {str(r.get("app_id")): r for r in data if r.get("app_id")}
        for r in rows:
            aid = r.get("app_id", "")
            if aid and aid in by_id:
                if not r.get("raw_conversation_summary"):
                    r["raw_conversation_summary"] = (by_id[aid].get("conversation_summary") or "").strip()
                # JSON typically has no scraped content; only pipeline stores it in conclusion file
    cols = ["app_id", "app_name", "app_url", "date", "sources_checked", "raw_conversation_summary", "raw_app_content", "app_summary", "step1_what_sold", "step2_comparison", "verdict", "reasoning", "non_compliant_subcategories"]
    out_csv = out_dir / "underwriting_results.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])
    print(f"Wrote {len(rows)} rows to {out_csv}")


if __name__ == "__main__":
    main()
