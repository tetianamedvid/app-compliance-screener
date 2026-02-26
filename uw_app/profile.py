"""
App profile as transposed table (field | value).
Stub: build from resolved app record (app_id, app_name, app_url, msid, wp_account_id, conversation_summary, etc.).
Production: run Trino full-app-profile query for app_id and transpose the single row.
Shows a single "WP account id" row; wp_account_id, account_id, and linked_wp_account_id are collapsed into one.
"""
from typing import Any, Optional

MAX_VALUE_LEN = 500  # truncate long values in transposed table

# Collapse these into a single "WP account id" row in the profile
WP_ACCOUNT_FIELD_KEYS = ("wp_account_id", "account_id", "linked_wp_account_id")
WP_ACCOUNT_DISPLAY_NAME = "WP account id"

# Omit from profile table; shown in Conversation history section instead
PROFILE_SKIP_KEYS = ("conversation_summary", "app_context_conversation_summary")

# Display names for profile table (internal key -> label)
FIELD_DISPLAY_NAMES = {
    "first_activity_at": "date app created",
    "user_apps_last_activity_at": "date app last updated",
    "user_description": "User Description",
    "user_app_events_count": "user_app_events_count",
    "integrations_count": "integrations_count",
    "agents": "agents",
    "agents_enabled": "agents_enabled",
    "app_info": "app_info",
    "app_publish_info": "app_publish_info",
    "app_stage": "app_stage",
    "app_type": "app_type",
    "backend_project": "backend_project",
    "captured_from_url": "captured_from_url",
    "categories": "Categories",
    "public_settings": "Public Settings",
}

# Preferred order for profile rows (these appear first when present; rest follow)
PROFILE_FIELD_ORDER = (
    "app_id",
    "app_name",
    "app_url",
    "msid",
    "wp_account_id",
    "account_id",
    "linked_wp_account_id",
    "user_description",
    "first_activity_at",
    "user_apps_last_activity_at",
    "user_app_events_count",
    "integrations_count",
    "agents",
    "agents_enabled",
    "app_info",
    "app_publish_info",
    "app_stage",
    "app_type",
    "backend_project",
    "captured_from_url",
    "categories",
    "public_settings",
)


def _truncate(v: Any) -> str:
    s = str(v) if v is not None else ""
    if len(s) > MAX_VALUE_LEN:
        return s[:MAX_VALUE_LEN] + "..."
    return s


def _wp_account_value(record: dict) -> Optional[str]:
    """First non-empty value among wp_account_id, account_id, linked_wp_account_id."""
    for k in WP_ACCOUNT_FIELD_KEYS:
        v = record.get(k)
        if v is not None and (not isinstance(v, str) or v.strip()):
            return str(v).strip()
    return None


def _field_label(key: str) -> str:
    """Display label for a profile field (e.g. first_activity_at -> date app created)."""
    return FIELD_DISPLAY_NAMES.get(key, key)


def _format_categories(val: Any) -> str:
    """Parse categories from Trino (JSON string or Mongo array) and return a comma-separated string for display (e.g. 'Finance, Education, Data & Analytics')."""
    import json
    if val is None:
        return ""
    if isinstance(val, list):
        parts = [str(x).strip() for x in val if str(x).strip()]
        return ", ".join(parts)
    s = str(val).strip()
    if not s:
        return ""
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return ", ".join(str(x).strip() for x in parsed if str(x).strip())
        if isinstance(parsed, dict):
            return ", ".join(str(x).strip() for x in parsed.values() if str(x).strip())
    except Exception:
        pass
    if s.startswith("[") and "]" in s:
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return ", ".join(str(x).strip() for x in parsed if str(x).strip())
        except Exception:
            pass
    return s


def _profile_rows_from_record(record: dict) -> list[dict]:
    """Build list of {field, value} from a record, collapsing WP account fields into one."""
    if not record:
        return []
    wp_val = _wp_account_value(record)
    seen_wp_row = False
    rows = []
    seen_keys = set()

    def emit(key: str, val: Any) -> None:
        nonlocal seen_wp_row
        if key in PROFILE_SKIP_KEYS:
            return
        if key in WP_ACCOUNT_FIELD_KEYS:
            seen_keys.update(WP_ACCOUNT_FIELD_KEYS)
            if not seen_wp_row and wp_val:
                rows.append({"field": WP_ACCOUNT_DISPLAY_NAME, "value": _truncate(wp_val)})
                seen_wp_row = True
            return
        if val is None or (isinstance(val, str) and not val.strip()):
            return
        seen_keys.add(key)
        display_val = _format_categories(val) if key == "categories" else val
        rows.append({"field": _field_label(key), "value": _truncate(display_val)})

    for key in PROFILE_FIELD_ORDER:
        if key in record:
            emit(key, record[key])
    for key, val in record.items():
        if key not in seen_keys:
            emit(key, val)
    return rows


def profile_from_app_record(record: dict) -> list[dict]:
    """
    Build transposed app profile from a single app record (e.g. from resolve).
    Returns list of {"field": str, "value": str} for UI. One row for WP account id.
    """
    return _profile_rows_from_record(record)


def profile_from_trino_row(row: dict) -> list[dict]:
    """
    Build transposed app profile from one Trino full-app-profile row.
    Use when Trino is connected; same output shape as profile_from_app_record. One row for WP account id.
    """
    return _profile_rows_from_record(row)
