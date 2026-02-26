"""
Quiet background refresh of the app list from a source file.
When APPS_REFRESH_SOURCE_PATH is set: on app open and at most once per hour,
re-import from that file into APPS_JSON_PATH. No dialogs, no extra UI unless you show "Last refreshed".
"""
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT_ROOT / "data" / ".last_apps_refresh"
DEFAULT_INTERVAL_SECONDS = 3600  # 1 hour


def _get_refresh_config():
    """Return (source_path, target_path, interval_seconds) or (None, None, None) if not configured."""
    raw = (os.environ.get("APPS_REFRESH_SOURCE_PATH") or "").strip()
    if not raw:
        return None, None, None
    source = Path(raw)
    if not source.is_absolute():
        source = PROJECT_ROOT / raw
    from .resolve import get_apps_json_path
    target = get_apps_json_path()
    interval = int(os.environ.get("APPS_REFRESH_EVERY_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))
    return source, target, interval


def _last_refresh_time() -> float:
    """Seconds since epoch of last refresh, or 0 if never."""
    if not STATE_FILE.exists():
        return 0.0
    try:
        return float(STATE_FILE.read_text().strip())
    except Exception:
        return 0.0


def _write_refresh_time():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(str(time.time()))


def run_refresh_if_due() -> bool:
    """
    If APPS_REFRESH_SOURCE_PATH is set and it's been at least APPS_REFRESH_EVERY_SECONDS
    (default 1 hour) since last refresh, re-import source -> APPS_JSON_PATH and update state.
    Returns True if a refresh was performed, False otherwise. No noise (no stderr unless script fails).
    """
    source, target, interval = _get_refresh_config()
    if source is None or target is None:
        return False
    if not source.exists():
        return False
    last = _last_refresh_time()
    if last > 0 and (time.time() - last) < interval:
        return False
    script = PROJECT_ROOT / "scripts" / "import_apps_export.py"
    if not script.exists():
        return False
    try:
        subprocess.run(
            [sys.executable, str(script), str(source), "--out", str(target)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=600,
        )
    except Exception:
        return False
    _write_refresh_time()
    return True


def get_last_refresh_time() -> float:
    """Seconds since epoch of last refresh, or 0 if never. For display only."""
    return _last_refresh_time()


def import_upload_to_app_list(upload_path: Path) -> tuple[bool, str]:
    """
    Run the import script: upload_path (CSV or JSON) -> APPS_JSON_PATH.
    Returns (True, message) on success, (False, error_message) on failure.
    """
    from .resolve import get_apps_json_path
    target = get_apps_json_path()
    if not upload_path.exists():
        return False, "Uploaded file not found."
    script = PROJECT_ROOT / "scripts" / "import_apps_export.py"
    if not script.exists():
        return False, "Import script not found."
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(upload_path), "--out", str(target)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "Import failed.").strip() or "Import failed."
        _write_refresh_time()
        return True, (result.stdout or f"Imported to {target.name}.").strip()
    except subprocess.TimeoutExpired:
        return False, "Import timed out."
    except Exception as e:
        return False, str(e)
