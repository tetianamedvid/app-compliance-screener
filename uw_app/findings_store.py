"""
Persistent findings store: every screened app is logged as a JSON line file.
Supports append, query, export. Thread-safe.
"""
from __future__ import annotations
import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE_PATH = PROJECT_ROOT / "data" / "findings.jsonl"

_lock = threading.Lock()
_pulled = False
_CONTENTS_API_MAX_BYTES = 900_000  # GitHub Contents API hard limit ~1MB


def _github_token() -> str:
    import os
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    try:
        import streamlit as st
        return st.secrets.get("GITHUB_TOKEN", "")
    except Exception:
        return ""


def _github_repo() -> str:
    import os
    return os.environ.get("GITHUB_REPO", "tetianamedvid/app-compliance-screener")


def _github_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "uw-app",
    }


def _parse_findings_bytes(data: bytes) -> list[dict]:
    rows: list[dict] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def _unique_url_count(rows: list[dict]) -> int:
    return len({_normalize_url(r.get("url", "")) for r in rows if _normalize_url(r.get("url", ""))})


def _merge_rows(local_rows: list[dict], remote_rows: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    no_url: list[dict] = []
    for row in local_rows:
        key = _normalize_url(row.get("url", ""))
        if key:
            merged[key] = row
        else:
            no_url.append(row)
    for row in remote_rows:
        key = _normalize_url(row.get("url", ""))
        if not key:
            no_url.append(row)
            continue
        if key in merged:
            old = merged[key]
            for k in ("review_status", "review_note", "review_updated", "correct_verdict"):
                if k in old and k not in row:
                    row[k] = old[k]
        merged[key] = row
    return list(merged.values()) + no_url


def _fetch_remote_bytes(token: str, repo: str, file_path: str = "data/findings.jsonl") -> tuple[bytes, dict]:
    """Return (file bytes, contents API metadata). Uses blob API for large files."""
    import base64
    import urllib.request

    headers = _github_headers(token)
    api_url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    req = urllib.request.Request(api_url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        meta = json.loads(resp.read())

    content_b64 = meta.get("content")
    if content_b64:
        return base64.b64decode(content_b64), meta

    blob_sha = meta.get("sha", "")
    blob_url = f"https://api.github.com/repos/{repo}/git/blobs/{blob_sha}"
    req2 = urllib.request.Request(blob_url, headers=headers)
    with urllib.request.urlopen(req2, timeout=60) as resp2:
        blob = json.loads(resp2.read())
    return base64.b64decode(blob.get("content", "")), meta


def _pull_from_github_once() -> None:
    """On first load, pull findings.jsonl from GitHub via git blob API (handles >1MB files).

    Merges remote rows into local to prevent data loss: if both local and remote
    have entries, they are deduplicated by URL (newer entry wins, review metadata
    preserved).
    """
    global _pulled
    if _pulled:
        return

    token = _github_token()
    if not token:
        _pulled = True
        return

    repo = _github_repo()
    file_path = "data/findings.jsonl"

    try:
        remote_bytes, meta = _fetch_remote_bytes(token, repo, file_path)
        if not remote_bytes:
            _pulled = True
            return

        remote_rows = _parse_findings_bytes(remote_bytes)
        local_rows = _parse_findings_bytes(STORE_PATH.read_bytes()) if STORE_PATH.exists() else []

        if not local_rows:
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STORE_PATH.write_bytes(remote_bytes)
            _pulled = True
            return

        local_urls = _unique_url_count(local_rows)
        remote_urls = _unique_url_count(remote_rows)
        if local_urls == remote_urls and len(local_rows) == len(remote_rows):
            _pulled = True
            return

        all_rows = _merge_rows(local_rows, remote_rows)
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            for row in all_rows:
                f.write(json.dumps(row, default=str) + "\n")

        _pulled = True
    except Exception:
        pass  # don't set _pulled — retry on next load_all()


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def make_batch_id() -> str:
    """Unique id for a screening run (used to filter UI to current batch)."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


_REVIEW_PRESERVE_KEYS = (
    "review_status", "review_note", "review_updated", "correct_verdict",
)


def _merge_row_into_store(rows: list[dict], result_dict: dict) -> None:
    """Upsert one result into an in-memory row list (preserves review metadata)."""
    url = _normalize_url(result_dict.get("url", ""))
    if not url:
        rows.append(result_dict)
        return

    for i, row in enumerate(rows):
        if _normalize_url(row.get("url", "")) == url:
            for key in _REVIEW_PRESERVE_KEYS:
                if key in row and key not in result_dict:
                    result_dict[key] = row[key]
            rows[i] = result_dict
            return
    rows.append(result_dict)


def append_many(results: list[dict], *, sync_github: bool = True) -> int:
    """Save multiple screening results in one read/write cycle.

    Returns the number of rows written/updated.
    """
    if not results:
        return 0

    rows = load_all()
    for result_dict in results:
        _merge_row_into_store(rows, result_dict)
    _rewrite(rows, sync_github=sync_github)
    return len(results)


def append(result_dict: dict) -> None:
    """Save a screening result. If the same URL was screened before, the old
    entry is replaced (preserving any existing review_status / review_note)."""
    append_many([result_dict])


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


_ARCHIVE_PATH = PROJECT_ROOT / "data" / "findings_archive.jsonl"


def load_all() -> list[dict]:
    """Load all findings from disk, deduplicated by URL (latest wins).

    Merges from findings_archive.jsonl (historical backup) to prevent
    data loss when the main file gets truncated on cold starts.
    """
    _pull_from_github_once()

    main_rows = _read_jsonl(STORE_PATH)
    archive_rows = _read_jsonl(_ARCHIVE_PATH)

    # Merge: archive as base, main overwrites (main has latest data)
    seen: dict[str, int] = {}
    deduped: list[dict] = []

    for row in archive_rows:
        key = _normalize_url(row.get("url", ""))
        if not key:
            deduped.append(row)
            continue
        if key not in seen:
            seen[key] = len(deduped)
            deduped.append(row)

    for row in main_rows:
        key = _normalize_url(row.get("url", ""))
        if not key:
            deduped.append(row)
            continue
        if key in seen:
            old = deduped[seen[key]]
            for k in ("review_status", "review_note", "review_updated", "correct_verdict"):
                if k in old and k not in row:
                    row[k] = old[k]
            deduped[seen[key]] = row
        else:
            seen[key] = len(deduped)
            deduped.append(row)

    return deduped


def load_recent(
    *,
    batch_id: str | None = None,
    since: date | datetime | str | None = None,
    include_archive: bool = False,
) -> list[dict]:
    """Load findings for UI display — fast path that skips archive by default.

    Args:
        batch_id: Only rows from this screening run.
        since: Only rows screened on/after this date (ISO date or datetime).
        include_archive: Merge findings_archive.jsonl (slow; use for full history).
    """
    _pull_from_github_once()

    if include_archive:
        rows = load_all()
    else:
        rows = _read_jsonl(STORE_PATH)

    if batch_id:
        rows = [r for r in rows if r.get("batch_id") == batch_id]

    if since is not None:
        if isinstance(since, datetime):
            since_d = since.date()
        elif isinstance(since, date):
            since_d = since
        else:
            since_d = date.fromisoformat(str(since)[:10])
        rows = [
            r for r in rows
            if r.get("screened_at")
            and date.fromisoformat(r["screened_at"][:10]) >= since_d
        ]

    return rows


def store_total_count(*, include_archive: bool = False) -> int:
    """Row count without building full merge (for captions)."""
    _pull_from_github_once()
    n = 0
    if STORE_PATH.exists():
        with STORE_PATH.open(encoding="utf-8") as f:
            n = sum(1 for line in f if line.strip())
    if include_archive and _ARCHIVE_PATH.exists():
        with _ARCHIVE_PATH.open(encoding="utf-8") as f:
            n += sum(1 for line in f if line.strip())
    return n


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
    "Needs Review": 3,
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


def _rewrite(rows: list[dict], *, sync_github: bool = True) -> None:
    """Rewrite entire store (used for updates)."""
    with _lock:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
    if sync_github:
        _sync_to_github()


def _sync_to_github_git_api(content: bytes, token: str, repo: str,
                            path: str = "data/findings.jsonl",
                            branch: str = "main") -> None:
    """Push large findings files via Git Data API (Contents API limit is ~1MB)."""
    import base64
    import urllib.request

    headers = _github_headers(token)

    ref_url = f"https://api.github.com/repos/{repo}/git/ref/heads/{branch}"
    req = urllib.request.Request(ref_url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        ref = json.loads(resp.read())
    head_sha = ref["object"]["sha"]

    commit_url = f"https://api.github.com/repos/{repo}/git/commits/{head_sha}"
    req = urllib.request.Request(commit_url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        head_commit = json.loads(resp.read())
    base_tree_sha = head_commit["tree"]["sha"]

    blob_body = json.dumps({
        "content": base64.b64encode(content).decode(),
        "encoding": "base64",
    }).encode()
    blob_url = f"https://api.github.com/repos/{repo}/git/blobs"
    req = urllib.request.Request(blob_url, data=blob_body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        blob = json.loads(resp.read())

    tree_body = json.dumps({
        "base_tree": base_tree_sha,
        "tree": [{
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        }],
    }).encode()
    tree_url = f"https://api.github.com/repos/{repo}/git/trees"
    req = urllib.request.Request(tree_url, data=tree_body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        tree = json.loads(resp.read())

    commit_body = json.dumps({
        "message": "Auto-save findings from live app",
        "tree": tree["sha"],
        "parents": [head_sha],
    }).encode()
    commit_url = f"https://api.github.com/repos/{repo}/git/commits"
    req = urllib.request.Request(commit_url, data=commit_body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        new_commit = json.loads(resp.read())

    ref_body = json.dumps({"sha": new_commit["sha"], "force": False}).encode()
    req = urllib.request.Request(ref_url, data=ref_body, headers=headers, method="PATCH")
    urllib.request.urlopen(req, timeout=30)


def _sync_to_github() -> None:
    """Push findings.jsonl to GitHub so data survives Streamlit Cloud reboots.

    Safety: refuses to push if the local store has fewer unique URLs than remote.
    Uses Git Data API for files larger than the Contents API limit (~1MB).
    """
    import base64
    import urllib.request

    token = _github_token()
    if not token:
        return

    repo = _github_repo()
    path = "data/findings.jsonl"
    headers = _github_headers(token)

    try:
        content = STORE_PATH.read_bytes()
        local_rows = _parse_findings_bytes(content)
        local_urls = _unique_url_count(local_rows)

        remote_urls = 0
        meta: dict = {}
        try:
            remote_bytes, meta = _fetch_remote_bytes(token, repo, path)
            remote_rows = _parse_findings_bytes(remote_bytes) if remote_bytes else []
            remote_urls = _unique_url_count(remote_rows)
        except Exception:
            remote_bytes = b""

        if remote_urls > local_urls:
            return  # never wipe a larger remote history with a smaller local cache

        if len(content) > _CONTENTS_API_MAX_BYTES:
            _sync_to_github_git_api(content, token, repo, path)
            return

        api_url = f"https://api.github.com/repos/{repo}/contents/{path}"
        sha = meta.get("sha", "")
        encoded = base64.b64encode(content).decode()
        body = json.dumps({
            "message": "Auto-save findings from live app",
            "content": encoded,
            "sha": sha,
        }).encode()
        req = urllib.request.Request(api_url, data=body, headers=headers, method="PUT")
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass


def export_csv(path: Optional[Path] = None) -> Path:
    """Export findings to CSV file on disk."""
    import csv
    path = path or (PROJECT_ROOT / "output" / "findings_export.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sort_findings(load_all())
    if not rows:
        path.write_text("No findings yet.\n")
        return path
    fields = _CSV_FIELDS
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return path


_CSV_FIELDS = [
    "url", "app_id", "app_name", "overall_verdict", "overall_color",
    "confidence", "top_category", "top_subcategory", "top_p_and_r_name",
    "app_description", "screened_at", "elapsed_seconds", "batch_id",
    "review_status", "review_note", "correct_verdict",
]


def export_csv_bytes(rows: list[dict] | None = None) -> bytes:
    """Return findings as CSV bytes for st.download_button.

    If rows is provided, export only that subset; otherwise export full store.
    """
    import csv
    import io
    data = sort_findings(rows if rows is not None else load_all())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for row in data:
        w.writerow(row)
    return buf.getvalue().encode("utf-8")
