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


def append(result_dict: dict) -> None:
    """Append one screening result to the findings store."""
    with _lock:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STORE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(result_dict, default=str) + "\n")


def load_all() -> list[dict]:
    """Load all findings from disk."""
    if not STORE_PATH.exists():
        return []
    rows = []
    for line in STORE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def count() -> int:
    if not STORE_PATH.exists():
        return 0
    return sum(1 for line in STORE_PATH.read_text(encoding="utf-8").splitlines() if line.strip())


def find_by_url(url: str) -> Optional[dict]:
    """Find the most recent result for a URL."""
    url = (url or "").strip().rstrip("/")
    for row in reversed(load_all()):
        if (row.get("url") or "").strip().rstrip("/") == url:
            return row
    return None


def find_by_app_id(app_id: str) -> Optional[dict]:
    for row in reversed(load_all()):
        if row.get("app_id") == app_id:
            return row
    return None


def update_review(url: str, status: str, note: str = "") -> bool:
    """Update review status and analyst note for a finding by URL."""
    rows = load_all()
    found = False
    for row in reversed(rows):
        if (row.get("url") or "").strip().rstrip("/") == url.strip().rstrip("/"):
            row["review_status"] = status
            row["review_note"] = note
            row["review_updated"] = datetime.now().isoformat(timespec="seconds")
            found = True
            break
    if found:
        _rewrite(rows)
    return found


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
    rows = load_all()
    if not rows:
        path.write_text("No findings yet.\n")
        return path
    fields = ["url", "app_id", "app_name", "overall_verdict", "overall_color",
              "confidence", "top_category", "top_subcategory", "app_description",
              "screened_at", "elapsed_seconds", "review_status", "review_note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return path
