"""
Resolve app_id from user input: app_id, msid, or wp_account_id.
Live: uses Trino (uw_app.trino_client) when TRINO_HOST is set — each lookup runs a Trino query.
Stub: when Trino not configured, loads from a local JSON file. No API keys used.
"""
import json
import os
from pathlib import Path
from typing import Optional

# Default path for stub data (same shape as production_test_apps.json)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Ensure .env is loaded so APPS_JSON_PATH is set no matter how the app was started
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
DEFAULT_APPS_JSON = PROJECT_ROOT / "data" / "production_test_apps.json"
# Optional: apps you add from the UI are appended here (merged with default list)
USER_APPS_JSON = PROJECT_ROOT / "data" / "user_apps.json"


def _apps_json_path() -> Path:
    """Use APPS_JSON_PATH from env if set (path to custom app list); else default. No API keys."""
    raw = os.environ.get("APPS_JSON_PATH", "").strip()
    if raw:
        p = Path(raw)
        if p.is_absolute():
            return p
        return PROJECT_ROOT / raw
    return DEFAULT_APPS_JSON


def get_apps_json_path() -> Path:
    """Return the main JSON file used for app lookups in stub mode (for UI)."""
    return _apps_json_path()


def _all_app_list_paths() -> list:
    """Paths to load for app list. Main list first, then user — so user (instructed/additions) overwrites main for same app_id."""
    paths = [_apps_json_path()]
    if USER_APPS_JSON.exists():
        paths.append(USER_APPS_JSON)
    return paths


def _resolve_via_trino(identifier_type: str, value: str) -> Optional[dict]:
    try:
        from . import trino_client
        if trino_client.is_configured():
            return trino_client.resolve(identifier_type, value)
    except Exception:
        pass
    return None


def _normalize_url(url: str) -> str:
    """Normalize URL for lookup (lowercase, strip, no trailing slash)."""
    if not url or not isinstance(url, str):
        return ""
    s = url.strip().lower().rstrip("/")
    if s.startswith("https://"):
        return s
    if s.startswith("http://"):
        return s
    if "://" not in s and ("." in s or "/" in s):
        return "https://" + s
    return s


def load_apps_index(path: Path) -> tuple[dict, dict, dict, dict]:
    """Load apps JSON and build indexes by app_id, msid, account_id (WP), app_url."""
    if not path.exists():
        return {}, {}, {}, {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_app_id = {}
    by_msid = {}
    by_account_id = {}
    by_app_url = {}
    for r in rows:
        aid = r.get("app_id")
        if aid:
            by_app_id[str(aid)] = r
        msid = r.get("msid")
        if msid:
            by_msid[str(msid)] = r
        for acc_key in ("account_id", "wp_account_id", "linked_wp_account_id"):
            acc = r.get(acc_key)
            if acc and str(acc).strip():
                by_account_id[str(acc).strip()] = r
        url = r.get("app_url")
        if url and str(url).strip():
            norm = _normalize_url(str(url))
            if norm:
                by_app_url[norm] = r
            by_app_url[str(url).strip()] = r
    return by_app_id, by_msid, by_account_id, by_app_url


def load_apps_index_merged(paths: Optional[list] = None) -> tuple[dict, dict, dict, dict]:
    """Load one or more JSON files and merge into single indexes by app_id, msid, account_id, app_url."""
    if paths is None:
        paths = _all_app_list_paths()
    by_app_id, by_msid, by_account_id, by_app_url = {}, {}, {}, {}
    for path in paths:
        if not path.exists():
            continue
        try:
            a, m, ac, u = load_apps_index(path)
            by_app_id.update(a)
            by_msid.update(m)
            by_account_id.update(ac)
            by_app_url.update(u)
        except Exception:
            pass
    return by_app_id, by_msid, by_account_id, by_app_url


def add_app_to_user_list(
    app_id: str,
    app_name: Optional[str] = None,
    app_url: Optional[str] = None,
    msid: Optional[str] = None,
    account_id: Optional[str] = None,
    conversation_summary: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Append one app to data/user_apps.json. Returns (True, None) on success, (False, error_message) on failure."""
    app_id = (app_id or "").strip()
    if not app_id:
        return False, "App ID is empty."
    try:
        entry = {
            "app_id": app_id,
            "app_name": app_name or "—",
            "app_url": app_url or "",
            "msid": msid,
            "account_id": account_id,
            "conversation_summary": conversation_summary or "",
        }
        USER_APPS_JSON.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if USER_APPS_JSON.exists():
            try:
                existing = json.loads(USER_APPS_JSON.read_text(encoding="utf-8"))
            except Exception:
                pass
        if not isinstance(existing, list):
            existing = []
        existing = [r for r in existing if (r.get("app_id") or "").strip() != app_id]
        existing.append(entry)
        USER_APPS_JSON.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        return True, None
    except Exception as e:
        return False, str(e)


def _normalize_row_for_ui(row: dict) -> dict:
    """Build UI record from a loaded row (JSON or Trino)."""
    out = {
        "app_id": row.get("app_id"),
        "app_name": row.get("app_name"),
        "app_url": row.get("app_url"),
        "conversation_summary": (row.get("conversation_summary") or "") if row.get("conversation_summary") is not None else "",
        "msid": row.get("msid"),
        "wp_account_id": row.get("account_id") or row.get("wp_account_id"),
    }
    for k, v in row.items():
        if k not in out and v is not None:
            out[k] = v
    return out


def resolve(
    identifier_type: str,
    value: str,
    apps_json_path: Optional[Path] = None,
) -> Optional[dict]:
    """
    Resolve to one app record with: app_id, app_name, app_url, conversation_summary, msid, wp_account_id.
    When APPS_JSON_PATH is set: prefer merged JSON first (so full local data wins). Else try Trino then JSON.
    """
    value = (value or "").strip()
    if not value:
        return None
    identifier_type = (identifier_type or "").strip().lower()
    if identifier_type not in ("app_id", "msid", "wp_account_id", "app_url"):
        return None

    # 1) If APPS_JSON_PATH is set, check the main list file first — use it when app is there (source of truth)
    main_path = _apps_json_path()
    if main_path.exists() and os.environ.get("APPS_JSON_PATH", "").strip():
        main_by_id, main_by_msid, main_by_acc, main_by_url = load_apps_index(main_path)
        main_row = None
        if identifier_type == "app_id":
            main_row = main_by_id.get(value)
        elif identifier_type == "msid":
            main_row = main_by_msid.get(value)
        elif identifier_type == "wp_account_id":
            main_row = main_by_acc.get(value)
        elif identifier_type == "app_url":
            main_row = main_by_url.get(value) or main_by_url.get(_normalize_url(value))
        if main_row and ((main_row.get("app_name") or "").strip() not in ("", "—") or (main_row.get("app_url") or "").strip()):
            return _normalize_row_for_ui(main_row)

    # 2) Load merged list (user + main) and use if we have a record with real data
    paths = [Path(p) for p in (apps_json_path,) if apps_json_path is not None] if apps_json_path is not None else None
    by_app_id, by_msid, by_account_id, by_app_url = load_apps_index_merged(paths)

    def _get_from_json():
        if identifier_type == "app_id":
            return by_app_id.get(value)
        if identifier_type == "msid":
            return by_msid.get(value)
        if identifier_type == "wp_account_id":
            return by_account_id.get(value)
        if identifier_type == "app_url":
            return by_app_url.get(value) or by_app_url.get(_normalize_url(value))
        return None

    json_row = _get_from_json()
    if json_row and (json_row.get("app_name") or "").strip() and (json_row.get("app_name") or "").strip() != "—":
        return _normalize_row_for_ui(json_row)

    # 3) Try Trino when configured
    row = _resolve_via_trino(identifier_type, value)
    if row:
        return _normalize_row_for_ui(row)

    # 4) Fall back to JSON (e.g. user_apps-only minimal record)
    if json_row:
        return _normalize_row_for_ui(json_row)

    return None
