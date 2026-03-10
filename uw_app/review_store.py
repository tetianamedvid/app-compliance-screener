"""
Persistent review status and analyst notes per app_id.
Stored as a single JSON file: data/review_notes.json
"""
import json
import threading
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE_PATH = PROJECT_ROOT / "data" / "review_notes.json"

_lock = threading.Lock()


def _load() -> dict:
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def get_review(app_id: str) -> dict:
    """Return {"status": str, "note": str, "updated_at": str} or empty dict."""
    data = _load()
    return data.get(app_id, {})


def get_all_reviews() -> dict:
    return _load()


def set_review(app_id: str, status: str, note: str = ""):
    from datetime import datetime
    with _lock:
        data = _load()
        data[app_id] = {
            "status": status,
            "note": note,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        _save(data)


VALID_STATUSES = ("Pending", "Reviewed", "Escalated", "Approved", "Declined")
