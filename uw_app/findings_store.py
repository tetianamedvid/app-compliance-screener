"""
Persistent findings store: every screened app is logged as a JSON line file.
Supports append, query, export. Thread-safe.
"""
from __future__ import annotations
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE_PATH = PROJECT_ROOT / "data" / "findings.jsonl"

_lock = threading.Lock()


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def append(result_dict: dict) -> None:
    """Save a screening result. If the same URL was screened before, the old
    entry is replaced (preserving any existing review_status / review_note)."""
    url = _normalize_url(result_dict.get("url", ""))
    rows = load_all()

    replaced = False
    for i, row in enumerate(rows):
        if _normalize_url(row.get("url", "")) == url:
            for key in ("review_status", "review_note", "review_updated"):
                if key in row and key not in result_dict:
                    result_dict[key] = row[key]
            rows[i] = result_dict
            replaced = True
            break

    if not replaced:
        rows.append(result_dict)

    _rewrite(rows)


def load_all() -> list[dict]:
    """Load all findings from disk, deduplicated by URL (latest wins)."""
    if not STORE_PATH.exists():
        return []
    raw: list[dict] = []
    for line in STORE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    seen: dict[str, int] = {}
    deduped: list[dict] = []
    for row in raw:
        key = _normalize_url(row.get("url", ""))
        if not key:
            deduped.append(row)
            continue
        if key in seen:
            old = deduped[seen[key]]
            for k in ("review_status", "review_note", "review_updated"):
                if k in old and k not in row:
                    row[k] = old[k]
            deduped[seen[key]] = row
        else:
            seen[key] = len(deduped)
            deduped.append(row)
    return deduped


def count() -> int:
    return len(load_all())


def find_by_url(url: str) -> Optional[dict]:
    """Find the result for a URL."""
    key = _normalize_url(url)
    for row in reversed(load_all()):
        if _normalize_url(row.get("url", "")) == key:
            return row
    return None


def find_by_app_id(app_id: str) -> Optional[dict]:
    for row in reversed(load_all()):
        if row.get("app_id") == app_id:
            return row
    return None


def update_review(url: str, status: str, note: str = "",
                   *, correct_verdict: str = "") -> bool:
    """Update review status, analyst note, and optional verdict override."""
    key = _normalize_url(url)
    rows = load_all()
    found = False
    for row in reversed(rows):
        if _normalize_url(row.get("url", "")) == key:
            row["review_status"] = status
            row["review_note"] = note
            row["review_updated"] = datetime.now().isoformat(timespec="seconds")
            if correct_verdict:
                row["correct_verdict"] = correct_verdict
            elif "correct_verdict" in row:
                del row["correct_verdict"]
            found = True
            break
    if found:
        _rewrite(rows)
    return found


_VERDICT_RANK = {
    "Not Supportable": 0,
    "Likely Not Supportable — Review": 1,
    "Not Enabled for Wix": 2,
    "Restricted — Review": 3,
    "Likely Supportable": 4,
    "Insufficient Data": 5,
}


def sort_findings(rows: list[dict]) -> list[dict]:
    """Sort findings: verdict severity first (Not Supportable on top),
    then confidence descending."""
    return sorted(
        rows,
        key=lambda f: (
            _VERDICT_RANK.get(f.get("overall_verdict", ""), 99),
            -(f.get("confidence") or 0),
        ),
    )


def _rewrite(rows: list[dict]) -> None:
    """Rewrite entire store (used for updates)."""
    with _lock:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")


def export_csv(path: Optional[Path] = None) -> Path:
    """Export findings to CSV."""
    import csv
    path = path or (PROJECT_ROOT / "output" / "findings_export.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sort_findings(load_all())
    if not rows:
        path.write_text("No findings yet.\n")
        return path
    fields = ["url", "app_id", "app_name", "overall_verdict", "overall_color",
              "confidence", "top_category", "top_subcategory", "app_description",
              "screened_at", "elapsed_seconds",
              "review_status", "review_note", "correct_verdict"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return path
