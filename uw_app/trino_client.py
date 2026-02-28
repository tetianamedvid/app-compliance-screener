"""
Trino client for live lookups. Used by the UW app — not MCP.
Supports SSO: when TRINO_HOST or TRINO_DEFAULT_HOST is set, uses OAuth2 so the first
query can open the browser for login; token is cached. No API keys.
"""
import os
import re
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from project root so TRINO_* are set without exporting in the terminal
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

# Use SSO (OAuth2) when connecting so users log in via browser; no API keys
_USE_SSO = os.environ.get("TRINO_USE_SSO", "true").strip().lower() in ("1", "true", "yes")

# Last error from Trino (so the app can show it when lookup returns nothing)
_last_trino_error: Optional[str] = None


def _normalize_error(exc: Exception) -> str:
    """Turn raw exception into a short message; detect 403/HTML so we can suggest wrong host."""
    raw = str(exc)
    if "403" in raw and ("Forbidden" in raw or "DOCTYPE html" in raw or "wix.com" in raw.lower()):
        host = os.environ.get("TRINO_HOST", "").strip() or os.environ.get("TRINO_DEFAULT_HOST", "")
        return (
            f"403 Forbidden from {host or 'TRINO_HOST'}. "
            "That host is likely the Quix/data platform web UI (e.g. bo.wix.com), not the Trino API. "
            "Get the actual Trino coordinator host from Quix or your data platform docs and set TRINO_HOST to that."
        )
    if "WWW-Authenticate" in raw:
        return (
            f"{raw} "
            "If TRINO_HOST is correct, try TRINO_USE_SSO=false in .env; or get the Trino coordinator host from Quix/data platform."
        )
    return raw


def get_last_trino_error() -> Optional[str]:
    """Return the last Trino error message, if any (so UI can show why live data failed)."""
    return _last_trino_error

# Resolve queries (one row: app_id, app_name, app_url, msid, wp_account_id, conversation_summary)
RESOLVE_BY_APP_ID = """
SELECT
  ba.app_id,
  ba.app_name,
  ba.app_url,
  ba.msid,
  ap.account_id AS wp_account_id,
  m.conversation_summary
FROM prod_encrypted.payments.pp_base44_apps_replica ba
LEFT JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
LEFT JOIN prod.marketing.base44_app_context_snapshots_mongo m ON m.app_id = ba.app_id
WHERE ba.app_id = ?
"""
RESOLVE_BY_MSID = """
SELECT
  ba.app_id,
  ba.app_name,
  ba.app_url,
  ba.msid,
  ap.account_id AS wp_account_id,
  m.conversation_summary
FROM prod_encrypted.payments.pp_base44_apps_replica ba
LEFT JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
LEFT JOIN prod.marketing.base44_app_context_snapshots_mongo m ON m.app_id = ba.app_id
WHERE ba.msid = ?
"""
RESOLVE_BY_WP_ACCOUNT = """
SELECT
  ba.app_id,
  ba.app_name,
  ba.app_url,
  ba.msid,
  ap.account_id AS wp_account_id,
  m.conversation_summary
FROM prod_encrypted.payments.pp_base44_apps_replica ba
INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
LEFT JOIN prod.marketing.base44_app_context_snapshots_mongo m ON m.app_id = ba.app_id
WHERE ap.account_id = ?
"""

# Distinct count of app_ids connected to WP (for validation: data full vs Trino source of truth)
WP_CONNECTED_APP_COUNT_SQL = """
SELECT COUNT(DISTINCT ba.app_id) AS cnt
FROM prod_encrypted.payments.pp_base44_apps_replica ba
INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
"""


def get_wp_connected_app_count() -> Optional[int]:
    """
    Return distinct count of app_ids connected to WP in Trino, or None if Trino unavailable/failed.
    Use with local app list size to validate that data are full.
    """
    row = _execute_one(WP_CONNECTED_APP_COUNT_SQL, ())
    if not row or "cnt" not in row:
        return None
    try:
        return int(row["cnt"])
    except (TypeError, ValueError):
        return None


def _conn_params() -> Optional[dict]:
    # Skip Trino entirely when user wants local-only (e.g. no working Trino host yet)
    if os.environ.get("TRINO_USE_LOCAL_ONLY", "").strip().lower() in ("1", "true", "yes"):
        return None
    host = os.environ.get("TRINO_HOST", "").strip() or os.environ.get("TRINO_DEFAULT_HOST", "").strip()
    if not host:
        return None
    port = int(os.environ.get("TRINO_PORT", "443"))
    catalog = os.environ.get("TRINO_CATALOG", "prod").strip() or "prod"
    user = os.environ.get("TRINO_USER", "uw_app").strip() or "uw_app"
    return {"host": host, "port": port, "catalog": catalog, "user": user}


def _make_trino_http_session():
    """Optional: path prefix (TRINO_PATH), User-Agent, and browser session (TRINO_COOKIE or TRINO_AUTH_HEADER) so the app can run as you in the browser."""
    import requests
    path_prefix = (os.environ.get("TRINO_PATH", "").strip() or "").rstrip("/")
    user_agent = os.environ.get("TRINO_USER_AGENT", "").strip() or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    cookie = (os.environ.get("TRINO_COOKIE", "").strip() or "").strip()
    auth_header = (os.environ.get("TRINO_AUTH_HEADER", "").strip() or "").strip()

    class _Session(requests.Session):
        def request(self, method, url, *args, **kwargs):
            if path_prefix:
                from urllib.parse import urlparse, urlunparse
                p = urlparse(url)
                new_path = path_prefix + p.path if not p.path.startswith(path_prefix) else p.path
                url = urlunparse((p.scheme, p.netloc, new_path, p.params, p.query, p.fragment))
            return super().request(method, url, *args, **kwargs)

    s = _Session()
    s.verify = True
    s.headers["User-Agent"] = user_agent
    if cookie:
        s.headers["Cookie"] = cookie
    if auth_header:
        s.headers["Authorization"] = auth_header
    return s


def _connect(params: dict):
    """Build Trino connection; uses OAuth2 (SSO) when _USE_SSO so browser opens for login. No API keys."""
    # Guard: never open a connection when local-only is set (avoids 403 from wrong host)
    if os.environ.get("TRINO_USE_LOCAL_ONLY", "").strip().lower() in ("1", "true", "yes"):
        raise RuntimeError("TRINO_USE_LOCAL_ONLY is set; Trino connection disabled.")
    import trino
    kwargs = {
        "host": params["host"],
        "port": params["port"],
        "user": params["user"],
        "catalog": params["catalog"],
        "http_scheme": "https",
        "legacy_prepared_statements": True,
        "http_session": _make_trino_http_session(),
    }
    if _USE_SSO:
        try:
            kwargs["auth"] = trino.auth.OAuth2Authentication()
        except Exception:
            pass  # fallback: no auth
    return trino.dbapi.connect(**kwargs)


def _execute_one(sql: str, params: tuple) -> Optional[dict]:
    """Run query and return first row as dict, or None on failure."""
    global _last_trino_error
    _last_trino_error = None
    params_conn = _conn_params()
    if not params_conn:
        return None
    cur = None
    conn = None
    try:
        conn = _connect(params_conn)
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return None
        columns = [d[0] for d in cur.description]
        return dict(zip(columns, row))
    except Exception as e:
        _last_trino_error = _normalize_error(e)
        return None
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass


def _execute_all(sql: str, params: tuple = ()) -> Optional[list]:
    """Run query and return all rows as list of dicts, or None on failure."""
    global _last_trino_error
    _last_trino_error = None
    params_conn = _conn_params()
    if not params_conn:
        return None
    cur = None
    conn = None
    try:
        conn = _connect(params_conn)
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        _last_trino_error = _normalize_error(e)
        return None
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass


# Batch: user activity per app for all WP-connected apps (for full_profiles build)
WP_USER_LOGS_SQL = """
SELECT u.app_id,
       COUNT(*) AS user_app_events_count,
       MIN(u.created_date) AS first_activity_at,
       MAX(u.created_date) AS last_activity_at
FROM prod.base44.base44_user_apps_logs_mongo u
WHERE u.app_id IN (
  SELECT ba.app_id
  FROM prod_encrypted.payments.pp_base44_apps_replica ba
  INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
)
GROUP BY u.app_id
ORDER BY u.app_id
"""

# Batch: conversation snapshots for all WP-connected apps
WP_CONVERSATION_SNAPSHOTS_SQL = """
SELECT m.app_id,
       m.updated_date,
       m.conversation_summary
FROM prod.marketing.base44_app_context_snapshots_mongo m
WHERE m.app_id IN (
  SELECT ba.app_id
  FROM prod_encrypted.payments.pp_base44_apps_replica ba
  INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
)
AND m.conversation_summary IS NOT NULL
ORDER BY m.app_id, m.updated_date ASC NULLS LAST
"""

# Batch: app metadata (user_description, public_settings, categories) from marketing mongo for all WP apps
WP_APP_METADATA_SQL = """
SELECT uga._id AS app_id,
       COALESCE(CAST(uga.user_description AS varchar), '') AS user_description,
       COALESCE(CAST(uga.public_settings AS varchar), '') AS public_settings,
       COALESCE(CAST(uga.categories AS varchar), '') AS categories
FROM prod.marketing.base44_user_generated_apps_v2_mongo uga
WHERE uga._id IN (
  SELECT ba.app_id
  FROM prod_encrypted.payments.pp_base44_apps_replica ba
  INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
)
ORDER BY uga._id
"""

# Earliest conversation preview per app from prod.base44.base44_conversation_messages_mongo
WP_EARLIEST_CONVERSATION_PREVIEW_SQL = """
WITH filter_app_ids AS (
  SELECT ba.app_id
  FROM prod_encrypted.payments.pp_base44_apps_replica ba
  INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
  WHERE ap.account_id IS NOT NULL
),
earliest_conversation_per_app AS (
  SELECT app_id, conversation_id AS earliest_conversation_id, first_ts AS earliest_conversation_first_at
  FROM (
    SELECT app_id, conversation_id, MIN(created_date) AS first_ts,
      ROW_NUMBER() OVER (PARTITION BY app_id ORDER BY MIN(created_date) NULLS LAST) AS rn
    FROM prod.base44.base44_conversation_messages_mongo
    WHERE app_id IS NOT NULL
    GROUP BY app_id, conversation_id
  ) t
  WHERE rn = 1
),
earliest_conversation_preview_per_app AS (
  SELECT e.app_id, e.earliest_conversation_first_at,
    array_join(array_agg(m.role || ': ' || substr(cast(coalesce(m.content, '') AS varchar), 1, 300) ORDER BY m.created_date), chr(10)) AS earliest_conversation_preview
  FROM earliest_conversation_per_app e
  JOIN (
    SELECT app_id, conversation_id, role, content, created_date,
      ROW_NUMBER() OVER (PARTITION BY app_id, conversation_id ORDER BY created_date) AS rn
    FROM prod.base44.base44_conversation_messages_mongo
  ) m ON m.app_id = e.app_id AND m.conversation_id = e.earliest_conversation_id AND m.rn <= 10
  GROUP BY e.app_id, e.earliest_conversation_first_at
)
SELECT f.app_id, ecp.earliest_conversation_first_at, ecp.earliest_conversation_preview
FROM filter_app_ids f
LEFT JOIN earliest_conversation_preview_per_app ecp ON ecp.app_id = f.app_id
WHERE ecp.earliest_conversation_preview IS NOT NULL
ORDER BY f.app_id
"""


def get_all_wp_user_logs() -> Optional[list]:
    """Return list of dicts (app_id, user_app_events_count, first_activity_at, last_activity_at) for all WP apps, or None."""
    rows = _execute_all(WP_USER_LOGS_SQL, ())
    if rows is None:
        return None
    # Normalize timestamps to strings for JSON
    out = []
    for r in rows:
        d = dict(r)
        for k in ("first_activity_at", "last_activity_at"):
            v = d.get(k)
            if v is not None and hasattr(v, "strftime"):
                d[k] = v.strftime("%Y-%m-%d %H:%M:%S")
            elif v is not None:
                d[k] = str(v)[:19].replace("T", " ")
        out.append(d)
    return out


def get_all_wp_conversation_snapshots() -> Optional[list]:
    """Return list of dicts (app_id, updated_date, conversation_summary) for all WP apps, or None."""
    rows = _execute_all(WP_CONVERSATION_SNAPSHOTS_SQL, ())
    if rows is None:
        return None
    out = []
    for r in rows:
        d = dict(r)
        ts = d.get("updated_date")
        if ts is not None and hasattr(ts, "strftime"):
            d["updated_date"] = ts.strftime("%Y-%m-%d %H:%M:%S")
        elif ts is not None:
            d["updated_date"] = str(ts)[:19].replace("T", " ")
        out.append(d)
    return out


def _normalize_categories(raw: Any) -> str:
    """Convert Trino/Mongo categories output to a clean JSON list of strings (e.g. ["Finance", "Education"])."""
    import json
    if raw is None:
        return "[]"
    s = str(raw).strip()
    if not s:
        return "[]"
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return json.dumps([str(x).strip() for x in parsed if str(x).strip()], ensure_ascii=False)
        if isinstance(parsed, dict):
            return json.dumps(list(parsed.values()) if parsed else [], ensure_ascii=False)
    except Exception:
        pass
    if s.startswith("[") and "]" in s:
        return s
    return json.dumps([s], ensure_ascii=False)


def get_all_wp_earliest_conversation_preview() -> Optional[list]:
    """Return list of dicts (app_id, earliest_conversation_first_at, earliest_conversation_preview) for WP apps with messages in prod.base44.base44_conversation_messages_mongo."""
    rows = _execute_all(WP_EARLIEST_CONVERSATION_PREVIEW_SQL, ())
    if rows is None:
        return None
    out = []
    for r in rows:
        d = dict(r)
        ts = d.get("earliest_conversation_first_at")
        if ts is not None and hasattr(ts, "strftime"):
            d["earliest_conversation_first_at"] = ts.strftime("%Y-%m-%d %H:%M:%S")
        elif ts is not None:
            d["earliest_conversation_first_at"] = str(ts)[:19].replace("T", " ")
        out.append(d)
    return out


def get_all_wp_app_metadata() -> Optional[list]:
    """Return list of dicts (app_id, user_description, public_settings, categories) for all WP apps from prod.marketing.base44_user_generated_apps_v2_mongo. categories is a clean JSON list string."""
    rows = _execute_all(WP_APP_METADATA_SQL, ())
    if rows is None:
        return None
    out = []
    for r in rows:
        d = dict(r)
        d["categories"] = _normalize_categories(d.get("categories"))
        out.append(d)
    return out


def resolve(identifier_type: str, value: str) -> Optional[dict]:
    """
    Run Trino resolve query. identifier_type: app_id | msid | wp_account_id.
    Returns one row as dict with keys app_id, app_name, app_url, msid, wp_account_id, conversation_summary, or None.
    """
    value = (value or "").strip()
    if not value:
        return None
    if identifier_type == "app_id":
        row = _execute_one(RESOLVE_BY_APP_ID, (value,))
    elif identifier_type == "msid":
        row = _execute_one(RESOLVE_BY_MSID, (value,))
    elif identifier_type == "wp_account_id":
        row = _execute_one(RESOLVE_BY_WP_ACCOUNT, (value,))
    else:
        return None
    if not row:
        return None
    # Normalize keys and ensure conversation_summary is str
    return {
        "app_id": row.get("app_id"),
        "app_name": row.get("app_name"),
        "app_url": row.get("app_url"),
        "conversation_summary": (row.get("conversation_summary") or "") if row.get("conversation_summary") is not None else "",
        "msid": row.get("msid"),
        "wp_account_id": row.get("wp_account_id"),
    }


def get_full_profile(app_id: str) -> Optional[dict]:
    """
    Run full-app-profile query for a single app_id. Returns one row as dict (many columns), or None.
    """
    params_conn = _conn_params()
    if not params_conn or not (app_id or "").strip():
        return None
    sql_path = PROJECT_ROOT / "docs" / "trino-query-full-app-profile-no-messages-column.sql"
    if not sql_path.exists():
        return None
    sql = sql_path.read_text(encoding="utf-8")
    # Replace filter_app_ids CTE with single-app filter (VALUES ('app_id'))
    safe_id = app_id.replace("'", "''")
    new_cte = f"WITH filter_app_ids AS (\n  SELECT app_id FROM (VALUES ('{safe_id}')) AS t(app_id)\n),\n"
    # Match from "WITH filter_app_ids AS (" to the first line that is exactly "),"
    sql = re.sub(
        r"WITH filter_app_ids AS\s*\(\s*\n.*?\n\),\s*\n",
        new_cte,
        sql,
        count=1,
        flags=re.DOTALL,
    )
    if "SELECT app_id FROM (VALUES" not in sql:
        return None
    cur = None
    conn = None
    try:
        conn = _connect(params_conn)
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        if not row:
            return None
        columns = [d[0] for d in cur.description]
        result = dict(zip(columns, row))
        if "categories" in result:
            result["categories"] = _normalize_categories(result.get("categories"))
        result.setdefault("user_description", "")
        result.setdefault("public_settings", "")
        result.setdefault("categories", "[]")
        return result
    except Exception as e:
        global _last_trino_error
        _last_trino_error = _normalize_error(e)
        return None
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass


def get_conversation_snapshots(app_id: str):
    """
    Fetch all conversation snapshots for an app from Trino (base44_app_context_snapshots_mongo).
    Returns list of dicts with keys: created_at (date/time string), content (full conversation_summary).
    Earliest first. Returns [] on failure or when Trino not configured.
    """
    params_conn = _conn_params()
    if not params_conn or not (app_id or "").strip():
        return []
    safe_id = app_id.replace("'", "''")
    sql = (
        "SELECT updated_date, conversation_summary "
        "FROM prod.marketing.base44_app_context_snapshots_mongo "
        f"WHERE app_id = '{safe_id}' AND conversation_summary IS NOT NULL "
        "ORDER BY updated_date ASC NULLS LAST"
    )
    cur = None
    conn = None
    try:
        conn = _connect(params_conn)
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        out = []
        for row in rows:
            d = dict(zip(columns, row))
            ts = d.get("updated_date")
            if ts is not None:
                try:
                    if hasattr(ts, "strftime"):
                        created_at = ts.strftime("%Y-%m-%d %H:%M")
                    else:
                        created_at = str(ts)[:19].replace("T", " ")
                except Exception:
                    created_at = str(ts)
            else:
                created_at = "—"
            content = (d.get("conversation_summary") or "").strip()
            if content:
                out.append({"created_at": created_at, "content": content})
        return out
    except Exception:
        return []
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass


def get_conversation_messages(app_id: str):
    """
    Fetch message-level data from prod.base44.base44_conversation_messages_mongo (created_date, role, content).
    ORDER BY created_date ASC. If that table returns no rows, fallback to prod.marketing.base44_app_context_snapshots_mongo (conversation_summary).
    Returns list of dicts with keys: created_at, role, content. [] on failure or when Trino not configured.
    """
    params_conn = _conn_params()
    if not params_conn or not (app_id or "").strip():
        return []
    safe_id = app_id.replace("'", "''")
    sql = (
        "SELECT created_date, role, content "
        "FROM prod.base44.base44_conversation_messages_mongo "
        f"WHERE app_id = '{safe_id}' "
        "ORDER BY created_date ASC NULLS LAST"
    )
    cur = None
    conn = None
    try:
        conn = _connect(params_conn)
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        out = []
        for row in rows:
            d = dict(zip(columns, row))
            ts = d.get("created_date")
            if ts is not None:
                try:
                    created_at = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)[:19].replace("T", " ")
                except Exception:
                    created_at = str(ts)[:19] if ts else "—"
            else:
                created_at = "—"
            role = (d.get("role") or "").strip() or "—"
            content = (d.get("content") or "").strip()
            out.append({
                "created_at": created_at,
                "role": role,
                "content": content,
            })
        if out:
            return out
    except Exception:
        pass
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass
    # Fallback: prod.marketing.base44_app_context_snapshots_mongo (conversation_summary)
    snapshots = get_conversation_snapshots(app_id)
    if not snapshots:
        return []
    return [
        {
            "created_at": s.get("created_at", "—"),
            "role": "snapshot",
            "content": (s.get("content") or "").strip(),
        }
        for s in snapshots
    ]


def test_connection() -> bool:
    """Run a trivial query to trigger SSO if needed (browser may open). Returns True if Trino is reachable."""
    global _last_trino_error
    _last_trino_error = None
    params_conn = _conn_params()
    if not params_conn:
        return False
    cur = None
    conn = None
    try:
        conn = _connect(params_conn)
        cur = conn.cursor()
        cur.execute("SELECT 1 AS ok")
        row = cur.fetchone()
        return row is not None
    except Exception as e:
        _last_trino_error = _normalize_error(e)
        return False
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass


def is_configured() -> bool:
    return _conn_params() is not None
