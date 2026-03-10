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

import json
import streamlit as st
import pandas as pd
from uw_app.app_screener import screen, screen_batch, ScreenResult
from uw_app import findings_store

REVIEW_STATUSES = ("Pending", "Reviewed", "Escalated", "Approved", "Declined")

st.set_page_config(
    page_title="App Compliance Screener",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""<style>
    .verdict-badge { display:inline-block; padding:6px 16px; border-radius:6px;
                     font-weight:bold; font-size:14px; color:white; }
    .verdict-red { background:#dc3545; }
    .verdict-orange { background:#fd7e14; }
    .verdict-green { background:#28a745; }
    .verdict-gray { background:#6c757d; }
    .kpi-box { text-align:center; padding:12px; border-radius:8px; background:#f8f9fa; }
    .kpi-num { font-size:28px; font-weight:bold; }
    .kpi-label { font-size:12px; color:#666; }
</style>""", unsafe_allow_html=True)


# ── Header ─────────────────────────────────────────────────────────────────────
st.title("🛡️ App Compliance Screener")
st.caption("Paste any app URL → instant scrape + policy classification → verdict. All results saved to findings table.")

# ── Findings Table (top) ────────────────────────────────────────────────────
all_findings = findings_store.load_all()

if all_findings:
    st.markdown("---")
    st.subheader("📋 Findings Table")
    st.caption("All screened apps — persistent across sessions. Sort, filter, review.")

    # KPIs
    total = len(all_findings)
    red_count = sum(1 for f in all_findings if f.get("overall_color") == "red")
    orange_count = sum(1 for f in all_findings if f.get("overall_color") == "orange")
    green_count = sum(1 for f in all_findings if f.get("overall_color") == "green")
    reviewed = sum(1 for f in all_findings if f.get("review_status") in ("Reviewed", "Approved", "Declined"))

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num">{total}</div><div class="kpi-label">Total Screened</div></div>', unsafe_allow_html=True)
    with k2:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#dc3545">{red_count}</div><div class="kpi-label">Not Supportable</div></div>', unsafe_allow_html=True)
    with k3:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#fd7e14">{orange_count}</div><div class="kpi-label">Restricted / Review</div></div>', unsafe_allow_html=True)
    with k4:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#28a745">{green_count}</div><div class="kpi-label">Likely Supportable</div></div>', unsafe_allow_html=True)
    with k5:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num">{reviewed}/{total}</div><div class="kpi-label">Reviewed</div></div>', unsafe_allow_html=True)

    st.markdown("")

    # Filters
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        verdict_options = ["All"] + sorted(set(f.get("overall_verdict", "") for f in all_findings))
        verdict_filter = st.selectbox("Verdict", verdict_options, key="ft_verdict")
    with fc2:
        review_options = ["All"] + list(REVIEW_STATUSES)
        review_filter = st.selectbox("Review Status", review_options, key="ft_review")
    with fc3:
        search = st.text_input("Search", placeholder="app name, URL, description…", key="ft_search")

    filtered = all_findings
    if verdict_filter != "All":
        filtered = [f for f in filtered if f.get("overall_verdict") == verdict_filter]
    if review_filter != "All":
        filtered = [f for f in filtered if f.get("review_status", "Pending") == review_filter]
    if search.strip():
        q = search.strip().lower()
        filtered = [f for f in filtered if
                    q in (f.get("url") or "").lower() or
                    q in (f.get("app_name") or "").lower() or
                    q in (f.get("app_description") or "").lower() or
                    q in (f.get("app_id") or "").lower() or
                    q in (f.get("top_category") or "").lower()]

    st.caption(f"Showing {len(filtered)} of {total}")

    # Build dataframe — P&R Index is main hierarchy
    rows = []
    for f in reversed(filtered):  # newest first
        pr_name = f.get("top_p_and_r_name") or (f"P&R #{f.get('top_p_and_r_id')}" if f.get("top_p_and_r_id") else "—")
        if pr_name == "—" and f.get("top_category"):
            pr_name = f"{f.get('top_category', '')} › {f.get('top_subcategory', '')}"
        rows.append({
            "URL": f.get("url", ""),
            "App Name": f.get("app_name") or "—",
            "Verdict": f.get("overall_verdict", ""),
            "Confidence": f.get("confidence", 0),
            "P&R": pr_name,
            "Category": f.get("top_category", "—"),
            "Subcategory": f.get("top_subcategory", "—"),
            "Description": (f.get("app_description") or "")[:120],
            "Review": f.get("review_status", "Pending"),
            "Note": f.get("review_note", ""),
            "Screened": f.get("screened_at", ""),
            "Time (s)": f.get("elapsed_seconds", 0),
        })

    df = pd.DataFrame(rows)

    def _color_verdict(val):
        v = (val or "").lower()
        if "not supportable" in v:
            return "background-color: #f8d7da; color: #721c24"
        if "not enabled" in v or "restricted" in v:
            return "background-color: #fff3cd; color: #856404"
        if "supportable" in v:
            return "background-color: #d4edda; color: #155724"
        if v == "error":
            return "background-color: #e2e3e5; color: #383d41"
        return ""

    def _color_review(val):
        v = (val or "").lower()
        if v == "approved":
            return "background-color: #d4edda; color: #155724"
        if v == "declined":
            return "background-color: #f8d7da; color: #721c24"
        if v == "escalated":
            return "background-color: #fff3cd; color: #856404"
        if v == "reviewed":
            return "background-color: #cce5ff; color: #004085"
        return ""

    styled = df.style.map(_color_verdict, subset=["Verdict"]).map(_color_review, subset=["Review"])

    st.dataframe(
        styled,
        width="stretch",
        height=min(700, 60 + 35 * len(df)),
        column_config={
            "URL": st.column_config.TextColumn("URL", width="medium"),
            "App Name": st.column_config.TextColumn("Name", width="small"),
            "Verdict": st.column_config.TextColumn("Verdict", width="medium"),
            "Confidence": st.column_config.ProgressColumn("Conf", min_value=0, max_value=100, format="%d%%"),
            "P&R": st.column_config.TextColumn("P&R Index", width="large"),
            "Category": st.column_config.TextColumn("Stripe Category", width="medium"),
            "Subcategory": st.column_config.TextColumn("Subcategory", width="medium"),
            "Description": st.column_config.TextColumn("Description", width="large"),
            "Review": st.column_config.TextColumn("Review", width="small"),
            "Note": st.column_config.TextColumn("Note", width="medium"),
            "Screened": st.column_config.TextColumn("When", width="small"),
            "Time (s)": st.column_config.NumberColumn("Speed", format="%.1f s"),
        },
        hide_index=True,
    )

    # Verdict distribution
    with st.expander("Verdict distribution"):
        vc = df["Verdict"].value_counts()
        st.bar_chart(vc)

    # Review editor
    st.markdown("---")
    st.subheader("Update Review")
    if filtered:
        url_options = [(f.get("url", ""), f.get("app_name") or f.get("url", "")) for f in reversed(filtered)]
        labels = [f"{name} ({url[:40]}…)" if len(url) > 40 else f"{name} ({url})" for url, name in url_options]
        sel_idx = st.selectbox("Select app", range(len(labels)), format_func=lambda i: labels[i], key="review_select")
        sel_url = url_options[sel_idx][0]
        current = findings_store.find_by_url(sel_url) or {}

        with st.form("review_form"):
            new_status = st.selectbox("Status", REVIEW_STATUSES,
                                      index=REVIEW_STATUSES.index(current.get("review_status", "Pending"))
                                      if current.get("review_status", "Pending") in REVIEW_STATUSES else 0)
            new_note = st.text_area("Analyst note", value=current.get("review_note", ""), height=80)
            if st.form_submit_button("💾 Save", type="primary"):
                findings_store.update_review(sel_url, new_status, new_note)
                st.success("Saved.")
                st.rerun()

    # Export
    st.markdown("---")
    ec1, ec2 = st.columns(2)
    with ec1:
        if st.button("📥 Export findings to CSV"):
            p = findings_store.export_csv()
            st.success(f"Exported to `{p}`")
    with ec2:
        if st.button("🗑️ Clear last results"):
            if "last_results" in st.session_state:
                del st.session_state["last_results"]
            st.rerun()

# ── Screen URL(s) ─────────────────────────────────────────────────────────────
st.markdown("---")
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
                match_lines = []
                for m in r.policy_matches[:5]:
                    sev = "🔴" if m["color"] == "red" else "🟡" if m["color"] == "orange" else "🟢"
                    kws = ", ".join(m.get("keywords", [])[:4])
                    pr = m.get("p_and_r_name") or f"P&R #{m.get('p_and_r_id', '?')}" if m.get("p_and_r_id") else None
                    stripe_part = f"{m['category']} › {m['subcategory']}"
                    if pr:
                        match_lines.append(
                            f"{sev} **{pr}** → {stripe_part} — "
                            f"*{m['verdict']}* ({m['confidence']}%)  `{kws}`"
                        )
                    else:
                        match_lines.append(
                            f"{sev} **{stripe_part}** — "
                            f"*{m['verdict']}* ({m['confidence']}%)  `{kws}`"
                        )
                with st.expander(f"Policy matches ({len(r.policy_matches)})", expanded=len(r.policy_matches) <= 3):
                    st.markdown("  \n".join(match_lines))

            # Structured signals
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

            # Page content summary (cleaned, not the raw dump)
            summary = getattr(r, "page_content_summary", "") or ""
            content_len = getattr(r, "content_length", 0) or 0
            if summary:
                with st.expander(f"Page content ({content_len:,} chars scraped)", expanded=False):
                    st.text(summary[:2500])

        st.markdown("---")


