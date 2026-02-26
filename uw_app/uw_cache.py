"""
Read underwriting conclusion for an app_id from cached conclusion files.
Searches output/run_*/conclusion_<app_id>.md and returns parsed UW result.
"""
import re
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"


def _parse_conclusion(text: str) -> dict:
    """Parse conclusion markdown into structured fields (same logic as conclusions_to_csv)."""
    row = {}
    m = re.search(r"\*\*Step 1 — What is sold:\*\*\s*\n(.*?)(?=\n\*\*Step 2|\Z)", text, re.DOTALL)
    row["step1_what_sold"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Step 2 — Comparison to policy:\*\*\s*\n(.*?)(?=\n\*\*Step 3|\Z)", text, re.DOTALL)
    row["step2_comparison"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Step 3 — Verdict:\*\*\s*\n?([^\n]+)", text)
    row["verdict"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Reasoning:\*\*\s*(.+?)(?=\n\*\*Non-compliant|\Z)", text, re.DOTALL)
    row["reasoning"] = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Non-compliant subcategories:\*\*\s*(.+)", text, re.DOTALL)
    row["non_compliant_subcategories"] = (m.group(1).strip() if m else "").strip()
    app_sec = re.search(r"## App summary \(middleman\)\s*\n(.*?)(?=\n## Policy comparison|\Z)", text, re.DOTALL)
    row["app_summary"] = app_sec.group(1).strip() if app_sec else ""
    # Old conclusions: "Restricted" + insufficient evidence => show as Manual Review Required (no-LLM intent)
    verdict_lower = (row.get("verdict") or "").strip().lower()
    reasoning_lower = (row.get("reasoning") or "").lower()
    sub_lower = (row.get("non_compliant_subcategories") or "").lower()
    if verdict_lower == "restricted" and (
        "insufficient evidence" in reasoning_lower or "offerings unknown" in sub_lower
    ):
        row["verdict"] = "Manual Review Required"
        row["reasoning"] = "Automatic analysis disabled (No LLM). Please review the evidences below manually."
    return row


def get_uw_for_app(app_id: str, run_id: Optional[str] = None) -> Optional[dict]:
    """
    Return UW result for app_id. If run_id is given, look only in output/run_<run_id>/.
    Otherwise search all output/run_*/ and return the first conclusion found (arbitrary order).
    """
    app_id = (app_id or "").strip()
    if not app_id:
        return None
    safe_id = app_id.replace("/", "_")
    name = f"conclusion_{safe_id}.md"

    if run_id:
        path = OUTPUT_DIR / f"run_{run_id}" / name
        if path.exists():
            return _parse_conclusion(path.read_text(encoding="utf-8"))
        return None
    # No run_id: find most recently modified conclusion file for this app
    candidates = []
    for d in OUTPUT_DIR.glob("run_*"):
        if not d.is_dir():
            continue
        path = d / name
        if path.exists():
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                pass
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return _parse_conclusion(candidates[0][1].read_text(encoding="utf-8"))
