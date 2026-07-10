"""
App Compliance Screener — paste URL(s), get instant verdicts, build findings table.
Run:  streamlit run streamlit_screener.py --server.port 8502
"""
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import streamlit as st

try:
    from uw_app.app_screener import screen, screen_batch, ScreenResult
    from uw_app import findings_store
    from uw_app.ui_helpers import (
        SCREENER_CSS, render_kpis, render_filters,
        build_findings_df, render_findings_table,
        render_findings_rows, render_policy_matches,
    )
except Exception as _import_err:
    st.error(f"Import failed: {_import_err}")
    import traceback
    st.code(traceback.format_exc())
    st.stop()

CHUNK_SIZE = 25
LARGE_BATCH_WARN = 100

st.set_page_config(
    page_title="App Compliance Screener",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(SCREENER_CSS, unsafe_allow_html=True)


@st.cache_data(ttl=600)
def _load_trino_context() -> dict[str, dict]:
    """Load Trino population data and build a URL -> context lookup."""
    p = ROOT / "data" / "trino_full_population.json"
    if not p.exists():
        return {}
    import json
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    lookup: dict[str, dict] = {}
    for row in data:
        url = (row.get("app_url") or "").strip().rstrip("/").lower()
        if url:
            lookup[url] = {
                "conversation_summary": row.get("conversation_summary") or "",
                "trino_description": row.get("trino_description") or "",
                "app_name_hint": row.get("trino_app_name") or "",
            }
    return lookup


def _get_trino_ctx(url: str) -> dict:
    ctx = _load_trino_context()
    key = (url or "").strip().rstrip("/").lower()
    return ctx.get(key, {})


def _batch_workers(n_urls: int) -> int:
    """Fewer workers on large batches / cloud to reduce memory pressure."""
    on_cloud = bool(
        os.environ.get("STREAMLIT_RUNTIME_ENV")
        or os.environ.get("STREAMLIT_SHARING_MODE")
    )
    if on_cloud or n_urls > LARGE_BATCH_WARN:
        return 4
    return 6


def _result_to_store_dict(r: ScreenResult, batch_id: str) -> dict:
    d = r.to_dict()
    for k in ("_body_text", "_deep_text"):
        d.pop(k, None)
    if d.get("app_description"):
        d["app_description"] = d["app_description"][:300]
    d.setdefault("review_status", "Pending")
    d.setdefault("review_note", "")
    d["batch_id"] = batch_id
    return d


def _normalize_urls(raw_urls: list[str]) -> list[str]:
    urls = []
    for u in raw_urls:
        u = u.strip().strip('"\'').rstrip(".,;)")
        if not u:
            continue
        if not u.startswith("http"):
            u = "https://" + u
        urls.append(u)
    return urls


_URL_IN_TEXT = re.compile(r"https?://[^\s,;\"'<>\[\]()]+", re.I)
_DOMAIN_LIKE = re.compile(
    r"[\w.-]+\.(?:base44\.app|base44\.com|velino\.org)(?:/[^\s,;\"'<>]*)?",
    re.I,
)


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        u = u.strip().strip('"\'').rstrip(".,;)")
        if not u:
            continue
        key = u.lower().rstrip("/")
        if not key.startswith("http"):
            key = "https://" + key
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out


def _extract_urls_from_text(text: str) -> list[str]:
    """Parse URLs from pasted text: one per line, CSV rows, or comma-separated."""
    if not text or not text.strip():
        return []

    found: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        found.extend(_URL_IN_TEXT.findall(line))

        segments = [line]
        if "," in line or ";" in line or "\t" in line:
            segments = re.split(r"[,;\t]", line)

        for part in segments:
            part = part.strip().strip('"\'')
            if not part:
                continue
            part_http = _URL_IN_TEXT.findall(part)
            if part_http:
                found.extend(part_http)
                continue
            found.extend(_DOMAIN_LIKE.findall(part))
            if any(d in part.lower() for d in (".base44.app", ".base44.com", ".velino.org")):
                found.append(part)
            elif part.startswith("http"):
                found.append(part)

    return _dedupe_urls(found)


def _extract_urls_from_bytes(data: bytes, filename: str) -> list[str]:
    """Parse URLs from an uploaded .txt, .csv, .tsv, or .json file."""
    import csv
    import io
    import json

    text = data.decode("utf-8", errors="replace")
    name = (filename or "").lower()

    if name.endswith(".json"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return _extract_urls_from_text(text)

        urls: list[str] = []
        items = obj if isinstance(obj, list) else obj.get("apps") or obj.get("data") or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict):
                    for key in ("app_url", "url", "appUrl", "URL", "link"):
                        val = item.get(key)
                        if val:
                            urls.append(str(val))
                            break
        if urls:
            return _dedupe_urls(urls)
        return _extract_urls_from_text(text)

    if name.endswith(".csv") or name.endswith(".tsv"):
        delim = "\t" if name.endswith(".tsv") else ","
        urls = []
        for row in csv.reader(io.StringIO(text), delimiter=delim):
            urls.extend(_extract_urls_from_text(" ".join(row)))
        return _dedupe_urls(urls) if urls else _extract_urls_from_text(text)

    return _extract_urls_from_text(text)


def _collect_urls_from_input(text: str, uploaded_file) -> list[str]:
    """Merge pasted text and optional uploaded file into one deduped URL list."""
    urls = _extract_urls_from_text(text or "")
    if uploaded_file is not None:
        file_urls = _extract_urls_from_bytes(
            uploaded_file.getvalue(), uploaded_file.name or "list.txt",
        )
        urls = _dedupe_urls(urls + file_urls)
    return urls


def _trino_rows_for_urls(urls: list[str]) -> list[dict]:
    rows = []
    for u in urls:
        ctx = _get_trino_ctx(u)
        if ctx:
            rows.append({"url": u, **ctx})
    return rows


def _process_batch_chunk(job: dict) -> None:
    """Screen one chunk and persist. Updates job in session_state."""
    urls: list[str] = job["urls"]
    index: int = job["index"]
    chunk_size: int = job.get("chunk_size", CHUNK_SIZE)
    batch_id: str = job["batch_id"]
    deep: bool = job.get("deep", False)

    chunk_urls = urls[index:index + chunk_size]
    if not chunk_urls:
        job["status"] = "complete"
        return

    trino_rows = _trino_rows_for_urls(chunk_urls)
    workers = _batch_workers(len(urls))
    results = screen_batch(
        chunk_urls,
        deep=deep,
        max_workers=workers,
        trino_rows=trino_rows if trino_rows else None,
    )

    store_rows = [_result_to_store_dict(r, batch_id) for r in results]
    findings_store.append_many(store_rows)

    job.setdefault("results", []).extend(results)
    job["index"] = index + len(chunk_urls)
    if job["index"] >= len(urls):
        job["status"] = "complete"
        st.session_state["active_batch_id"] = batch_id
        st.session_state["last_results"] = job["results"]
        st.session_state["findings_view"] = "Current batch"


# ── Header ─────────────────────────────────────────────────────────────────────
st.title("🛡️ App Compliance Screener")
st.caption(
    "Paste a full URL list or upload a file (.txt / .csv) — then screen hundreds or "
    "thousands of apps in one batch."
)

# ── Active batch job (chunked runner) ─────────────────────────────────────────
batch_job = st.session_state.get("batch_job")
if batch_job and batch_job.get("status") == "running":
    total = len(batch_job["urls"])
    done = batch_job.get("index", 0)
    st.info(f"Batch screening in progress: **{done} / {total}** URLs")
    st.progress(min(done / total, 1.0) if total else 0.0)

    stop_col, _ = st.columns([1, 4])
    with stop_col:
        if st.button("⏹ Stop batch", key="stop_batch"):
            batch_job["status"] = "stopped"
            if batch_job.get("results"):
                st.session_state["last_results"] = batch_job["results"]
                st.session_state["active_batch_id"] = batch_job.get("batch_id")
            st.rerun()

    _process_batch_chunk(batch_job)
    if batch_job.get("status") == "running":
        st.rerun()
    elif batch_job.get("status") == "complete":
        st.success(f"Batch complete — {len(batch_job.get('results', []))} apps screened.")
        del st.session_state["batch_job"]
        st.rerun()
    elif batch_job.get("status") == "stopped":
        st.warning("Batch stopped. Partial results were saved.")
        del st.session_state["batch_job"]
        st.rerun()

# ── Screen URL(s) — bulk paste or file upload ─────────────────────────────────
uploaded_list = st.file_uploader(
    "Upload URL list (.txt, .csv, .tsv, .json)",
    type=["txt", "csv", "tsv", "json"],
    key="url_list_file",
    help="One URL per line, or a CSV/JSON export with an app_url / url column.",
)

urls_input = st.text_area(
    "Paste URL list (all at once)",
    placeholder=(
        "Paste your full list here — one URL per line.\n"
        "Also works: comma-separated, tab-separated, or copied from Excel/Sheets.\n\n"
        "https://app-one.base44.app\n"
        "https://app-two.base44.app\n"
        "…"
    ),
    height=320,
    key="urls_input",
)

preview_urls = _collect_urls_from_input(urls_input, uploaded_list)
if preview_urls:
    st.info(f"**{len(preview_urls):,}** URLs loaded — click Screen to start.")

col1, col2, col3 = st.columns([1, 2, 1])
with col1:
    deep_mode = st.checkbox(
        "Deep scrape (Playwright)", value=False,
        help="Off = fast API-only (~2-3s). On = full browser render (~8-15s). "
             "Turn off for bulk runs (100+ URLs).",
    )
with col2:
    submitted = st.button("🔍 Screen", type="primary", use_container_width=True)
with col3:
    if preview_urls and st.button("Clear list", use_container_width=True):
        st.session_state["urls_input"] = ""
        st.rerun()

if submitted and not (batch_job and batch_job.get("status") == "running"):
    raw_urls = _collect_urls_from_input(urls_input, uploaded_list)
    if not raw_urls:
        st.warning("Paste or upload at least one URL.")
    else:
        urls = _normalize_urls(raw_urls)

        if len(urls) > LARGE_BATCH_WARN and deep_mode:
            st.warning(
                f"You pasted **{len(urls)}** URLs with deep scrape on. "
                "For bulk runs, turn off **Deep scrape** — otherwise this may take hours."
            )

        batch_id = findings_store.make_batch_id()

        if len(urls) == 1:
            ctx = _get_trino_ctx(urls[0])
            with st.spinner(f"Screening {urls[0]}…"):
                result = screen(urls[0], deep=deep_mode, **ctx)
            st.session_state["last_results"] = [result]
            findings_store.append_many([_result_to_store_dict(result, batch_id)])
            st.session_state["active_batch_id"] = batch_id
            st.session_state["findings_view"] = "Current batch"
            st.rerun()
        else:
            st.session_state["batch_job"] = {
                "urls": urls,
                "batch_id": batch_id,
                "index": 0,
                "chunk_size": CHUNK_SIZE,
                "deep": deep_mode,
                "status": "running",
                "results": [],
            }
            st.rerun()

# ── Last screening results (compact cards) ────────────────────────────────────
if st.session_state.get("last_results"):
    results: list[ScreenResult] = st.session_state["last_results"]
    st.markdown("---")
    st.subheader(f"Latest screening — {len(results)} app(s)")

    show_cards = min(len(results), 50)
    if len(results) > show_cards:
        st.caption(f"Showing first {show_cards} of {len(results)} — use Findings Table for full list.")

    for r in results[:show_cards]:
        color_class = f"verdict-{r.overall_color}"
        badge = f'<span class="verdict-badge {color_class}">{r.overall_verdict}</span>'

        with st.container():
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                name = r.app_name or r.url
                st.markdown(f"**{name}**")
                st.caption(f"`{r.url}`  •  {r.elapsed_seconds}s  •  ID: `{r.app_id or '—'}`")
            with c2:
                if r.app_description:
                    st.caption((r.app_description or "")[:180])
            with c3:
                st.markdown(badge, unsafe_allow_html=True)
                st.caption(f"Conf: {r.confidence}%")

            if r.error:
                st.error(r.error)

            if r.policy_matches:
                render_policy_matches(r.policy_matches)

            signals = []
            if r.entity_types:
                signals.append(("Data entities", ", ".join(r.entity_types[:8])))
            if r.payment_signals:
                signals.append(("Payments", ", ".join(r.payment_signals[:5])))
            if getattr(r, "login_methods", None):
                signals.append(("Auth", ", ".join(r.login_methods[:5])))
            if getattr(r, "features", None):
                signals.append(("Features", ", ".join(r.features[:6])))
            if getattr(r, "integrations", None):
                signals.append(("Integrations", ", ".join(r.integrations[:5])))
            if getattr(r, "visibility", None):
                signals.append(("Visibility", str(r.visibility)))
            if getattr(r, "data_sources", None):
                signals.append(("Sources", ", ".join(r.data_sources)))
            if signals:
                with st.expander("Signals & metadata", expanded=False):
                    for label, val in signals:
                        st.markdown(f"**{label}:** {val}")

            summary = getattr(r, "page_content_summary", "") or ""
            content_len = getattr(r, "content_length", 0) or 0
            if summary:
                with st.expander(f"Page content ({content_len:,} chars scraped)", expanded=False):
                    st.text(summary[:2500])

        st.markdown("---")

# ── Findings Table ─────────────────────────────────────────────────────────────
store_total = findings_store.store_total_count()
_dbg_store = findings_store.STORE_PATH
_dbg_exists = _dbg_store.exists()
_dbg_size = _dbg_store.stat().st_size if _dbg_exists else 0

active_batch = st.session_state.get("active_batch_id")
view_options = ["Current batch", "Today", "All history"]
default_view = st.session_state.get("findings_view")
if default_view not in view_options:
    default_view = "Current batch" if active_batch else "Today"

view_col, cap_col = st.columns([2, 3])
with view_col:
    findings_view = st.radio(
        "Show findings",
        view_options,
        index=view_options.index(default_view),
        horizontal=True,
        key="findings_view_radio",
    )
st.session_state["findings_view"] = findings_view

if findings_view == "Current batch":
    if active_batch:
        display_findings = findings_store.load_recent(batch_id=active_batch)
    else:
        display_findings = []
elif findings_view == "Today":
    display_findings = findings_store.load_recent(since=date.today())
else:
    display_findings = findings_store.load_all()

st.caption(
    f"📂 Store: {_dbg_size:,} bytes · {store_total:,} total on disk · "
    f"viewing **{findings_view.lower()}** ({len(display_findings)} rows) · build 2026-07-10"
)

if display_findings:
    hdr1, hdr2 = st.columns([3, 1])
    with hdr1:
        st.subheader("📋 Findings Table")
    with hdr2:
        detailed_view = st.toggle(
            "Detailed view", value=False, key="detailed_toggle",
            help="Toggle between compact table and per-row expandable details.",
        )
    st.caption("Sort, filter, and review. Large lists are paginated (100 per page).")

    render_kpis(display_findings)
    st.markdown("")

    filtered = render_filters(display_findings, key_prefix="ft")
    st.caption(f"Showing {len(filtered)} of {len(display_findings)} in this view")

    if "findings_page" not in st.session_state:
        st.session_state["findings_page"] = 0

    if detailed_view:
        new_page = render_findings_rows(
            filtered, key_prefix="fr", page=st.session_state["findings_page"],
        )
    else:
        df, url_list = build_findings_df(filtered)
        new_page = render_findings_table(
            df, url_list, key="findings_table", findings=filtered,
            page=st.session_state["findings_page"],
        )

    if new_page != st.session_state["findings_page"]:
        st.session_state["findings_page"] = new_page
        st.rerun()

    bc1, bc2, bc3 = st.columns(3)
    with bc1:
        view_csv = findings_store.export_csv_bytes(filtered)
        st.download_button(
            label="📥 Download current view (CSV)",
            data=view_csv,
            file_name="findings_view.csv",
            mime="text/csv",
            key="export_view_btn",
        )
    with bc2:
        if st.button("🗑️ Clear last results", key="clear_btn"):
            if "last_results" in st.session_state:
                del st.session_state["last_results"]
            st.rerun()
    with bc3:
        with st.expander("Export full history"):
            full_csv = findings_store.export_csv_bytes()
            st.download_button(
                label="📥 Download all findings (CSV)",
                data=full_csv,
                file_name="findings_export.csv",
                mime="text/csv",
                key="export_full_btn",
            )
else:
    if findings_view == "Current batch" and not active_batch:
        st.info("No current batch. Screen URLs above to start.")
    else:
        st.info("No findings in this view. Screen a URL above or change the view filter.")
