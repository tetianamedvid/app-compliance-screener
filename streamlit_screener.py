"""
App Compliance Screener — paste URL(s), get instant verdicts, build findings table.
Run:  streamlit run streamlit_screener.py --server.port 8502
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import streamlit as st
from uw_app.app_screener import screen, screen_batch, ScreenResult
from uw_app import findings_store
from uw_app.ui_helpers import (
    SCREENER_CSS, render_kpis, render_filters,
    build_findings_df, render_findings_table,
    render_findings_rows, render_policy_matches,
)

st.set_page_config(
    page_title="App Compliance Screener",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(SCREENER_CSS, unsafe_allow_html=True)

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("🛡️ App Compliance Screener")
st.caption("Paste any app URL → instant scrape + policy classification → verdict. All results saved to findings table.")

# ── Screen URL(s) — always at the top ─────────────────────────────────────────
with st.form("screen_form", clear_on_submit=False):
    urls_input = st.text_area(
        "App URL(s) — one per line",
        placeholder="https://my-app.base44.app\nhttps://another-app.base44.app",
        height=100,
        key="urls_input",
    )
    col1, col2 = st.columns([1, 3])
    with col1:
        deep_mode = st.checkbox("Deep scrape (Playwright)", value=False,
                                help="Off = fast API-only (~2-3s). On = full browser render (~8-15s).")
    with col2:
        submitted = st.form_submit_button("🔍 Screen", type="primary", use_container_width=True)

if submitted:
    raw_urls = [u.strip() for u in (urls_input or "").splitlines() if u.strip()]
    if not raw_urls:
        st.warning("Paste at least one URL.")
    else:
        urls = []
        for u in raw_urls:
            if not u.startswith("http"):
                u = "https://" + u
            urls.append(u)

        if len(urls) == 1:
            with st.spinner(f"Screening {urls[0]}…"):
                result = screen(urls[0], deep=deep_mode)
            st.session_state["last_results"] = [result]
        else:
            with st.spinner(f"Screening {len(urls)} URLs in parallel…"):
                results = screen_batch(urls, deep=deep_mode)
            st.session_state["last_results"] = results

        for r in st.session_state["last_results"]:
            d = r.to_dict()
            for k in ("_body_text", "_deep_text"):
                d.pop(k, None)
            if d.get("app_description"):
                d["app_description"] = d["app_description"][:300]
            d.setdefault("review_status", "Pending")
            d.setdefault("review_note", "")
            findings_store.append(d)

        st.rerun()

# ── Last screening results (compact cards) ────────────────────────────────────
if st.session_state.get("last_results"):
    results: list[ScreenResult] = st.session_state["last_results"]
    st.markdown("---")
    st.subheader(f"Latest screening — {len(results)} app(s)")

    for r in results:
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
all_findings = findings_store.load_all()

if all_findings:
    hdr1, hdr2 = st.columns([3, 1])
    with hdr1:
        st.subheader("📋 Findings Table")
    with hdr2:
        detailed_view = st.toggle("Detailed view", value=False, key="detailed_toggle",
                                  help="Toggle between compact table and per-row expandable details.")
    st.caption("All screened apps — persistent across sessions. Sort, filter, review.")

    render_kpis(all_findings)
    st.markdown("")

    filtered = render_filters(all_findings, key_prefix="ft")
    total = len(all_findings)
    st.caption(f"Showing {len(filtered)} of {total}")

    if detailed_view:
        render_findings_rows(filtered)
    else:
        df, url_list = build_findings_df(filtered)
        render_findings_table(df, url_list, key="findings_table")

    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("📥 Export findings to CSV", key="export_btn"):
            p = findings_store.export_csv()
            st.success(f"Exported to `{p}`")
    with bc2:
        if st.button("🗑️ Clear last results", key="clear_btn"):
            if "last_results" in st.session_state:
                del st.session_state["last_results"]
            st.rerun()
else:
    st.info("No findings yet. Screen a URL above to get started.")
