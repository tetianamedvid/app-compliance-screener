"""Review — analyst feedback loop for screened apps."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import streamlit as st
from uw_app import findings_store
from uw_app.ui_helpers import SCREENER_CSS, render_policy_matches, REVIEW_STATUSES
VERDICT_OPTIONS = [
    "—",
    "Not Supportable",
    "Likely Not Supportable — Review",
    "Not Enabled for Wix",
    "Restricted — Review",
    "Likely Supportable",
    "Insufficient Data",
]

st.set_page_config(
    page_title="Review | UW Lookup",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="auto",
)
st.markdown(SCREENER_CSS, unsafe_allow_html=True)

st.title("📝 Review Findings")
st.caption("Review screened apps, set status, add analyst notes, override verdicts.")

all_findings = findings_store.load_all()

if not all_findings:
    st.info("No findings yet. Screen some URLs in the Screener tab first.")
    st.stop()

# ── KPIs (review-specific + accuracy) ────────────────────────────────────────
total = len(all_findings)
pending = sum(1 for f in all_findings if f.get("review_status", "Pending") == "Pending")
reviewed = sum(1 for f in all_findings if f.get("review_status") == "Reviewed")
approved = sum(1 for f in all_findings if f.get("review_status") == "Approved")
declined = sum(1 for f in all_findings if f.get("review_status") == "Declined")
escalated = sum(1 for f in all_findings if f.get("review_status") == "Escalated")

confirmed_count = approved + reviewed
disputed_count = declined
total_reviewed = confirmed_count + disputed_count

k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
with k1:
    st.markdown(f'<div class="kpi-box"><div class="kpi-num">{total}</div>'
                f'<div class="kpi-label">Total</div></div>',
                unsafe_allow_html=True)
with k2:
    st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#6c757d">'
                f'{pending}</div><div class="kpi-label">Pending</div></div>',
                unsafe_allow_html=True)
with k3:
    st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#004085">'
                f'{reviewed}</div><div class="kpi-label">Reviewed</div></div>',
                unsafe_allow_html=True)
with k4:
    st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#28a745">'
                f'{approved}</div><div class="kpi-label">Approved</div></div>',
                unsafe_allow_html=True)
with k5:
    st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#dc3545">'
                f'{declined}</div><div class="kpi-label">Declined</div></div>',
                unsafe_allow_html=True)
with k6:
    st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#856404">'
                f'{escalated}</div><div class="kpi-label">Escalated</div></div>',
                unsafe_allow_html=True)
with k7:
    if total_reviewed >= 5:
        accuracy_pct = round(100 * confirmed_count / total_reviewed)
        color = "#28a745" if accuracy_pct >= 80 else "#fd7e14" if accuracy_pct >= 60 else "#dc3545"
        st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:{color}">'
                    f'{accuracy_pct}%</div><div class="kpi-label">Accuracy '
                    f'({confirmed_count}/{total_reviewed})</div></div>',
                    unsafe_allow_html=True)
    else:
        need = 5 - total_reviewed
        st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#aaa">—</div>'
                    f'<div class="kpi-label">Accuracy (need {need} more reviews)</div></div>',
                    unsafe_allow_html=True)

st.markdown("")

# ── Filters ───────────────────────────────────────────────────────────────────
fc1, fc2, fc3 = st.columns(3)
with fc1:
    review_options = ["All"] + list(REVIEW_STATUSES)
    review_filter = st.selectbox("Review Status", review_options, key="rv_status")
with fc2:
    verdict_options = ["All"] + sorted(set(f.get("overall_verdict", "") for f in all_findings))
    verdict_filter = st.selectbox("Verdict", verdict_options, key="rv_verdict")
with fc3:
    search = st.text_input("Search", placeholder="app name, URL, description…", key="rv_search")

filtered = all_findings
if review_filter != "All":
    filtered = [f for f in filtered if f.get("review_status", "Pending") == review_filter]
if verdict_filter != "All":
    filtered = [f for f in filtered if f.get("overall_verdict") == verdict_filter]
if search.strip():
    q = search.strip().lower()
    filtered = [f for f in filtered if
                q in (f.get("url") or "").lower() or
                q in (f.get("app_name") or "").lower() or
                q in (f.get("app_description") or "").lower() or
                q in (f.get("top_category") or "").lower() or
                q in (f.get("top_p_and_r_name") or "").lower()]

sorted_filtered = findings_store.sort_findings(filtered)
st.caption(f"Showing {len(sorted_filtered)} of {total}")

# ── Review queue ──────────────────────────────────────────────────────────────
if not sorted_filtered:
    st.info("No findings match your filters.")
    st.stop()

url_options = [(f.get("url", ""), f.get("app_name") or f.get("url", ""),
                f.get("overall_verdict", ""), f.get("confidence", 0),
                f.get("review_status", "Pending"))
               for f in sorted_filtered]
labels = [f"[{status}] [{verdict} {conf}%] {name}"
          for _, name, verdict, conf, status in url_options]

from urllib.parse import unquote
preselect_url = unquote(st.query_params.get("app", ""))
default_idx = 0
if preselect_url:
    for i, (u, *_) in enumerate(url_options):
        if u.rstrip("/").lower() == preselect_url.rstrip("/").lower():
            default_idx = i
            break

sel_idx = st.selectbox("Select app to review", range(len(labels)),
                       index=default_idx,
                       format_func=lambda i: labels[i], key="rv_select")
sel_url = url_options[sel_idx][0]
current = findings_store.find_by_url(sel_url) or {}

# ── App detail card ───────────────────────────────────────────────────────────
st.markdown("---")

dc1, dc2 = st.columns([3, 1])
with dc1:
    st.markdown(f"### {current.get('app_name') or current.get('url', '—')}")
    st.caption(f"`{current.get('url', '')}`")
with dc2:
    verdict = current.get("overall_verdict", "—")
    color = current.get("overall_color", "gray")
    color_map = {"red": "#dc3545", "orange": "#fd7e14", "green": "#28a745", "gray": "#6c757d"}
    st.markdown(
        f'<div style="text-align:right;padding:8px 16px;border-radius:8px;'
        f'background:{color_map.get(color,"#6c757d")};color:white;font-weight:bold;">'
        f'{verdict} ({current.get("confidence", 0)}%)</div>',
        unsafe_allow_html=True)

ic1, ic2, ic3 = st.columns(3)
with ic1:
    st.markdown(f"**P&R Index:** {current.get('top_p_and_r_name', '—')}")
with ic2:
    st.markdown(f"**Stripe Category:** {current.get('top_category', '—')}")
with ic3:
    st.markdown(f"**Subcategory:** {current.get('top_subcategory', '—')}")

if current.get("app_description"):
    st.caption(current["app_description"][:300])

matches = current.get("policy_matches", [])
if matches:
    render_policy_matches(matches)

# ── Review form ───────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Review")

existing_override = current.get("correct_verdict", "")
override_default = VERDICT_OPTIONS.index(existing_override) if existing_override in VERDICT_OPTIONS else 0

with st.form("review_form"):
    fc1, fc2 = st.columns(2)
    with fc1:
        new_status = st.selectbox("Status", REVIEW_STATUSES,
                                  index=REVIEW_STATUSES.index(current.get("review_status", "Pending"))
                                  if current.get("review_status", "Pending") in REVIEW_STATUSES else 0)
    with fc2:
        correct_verdict = st.selectbox(
            "Correct verdict (analyst override)",
            VERDICT_OPTIONS,
            index=override_default,
            help="If the screener got it wrong, select what the verdict should be. "
                 "This is tracked as training data for accuracy measurement.")
    new_note = st.text_area("Analyst note", value=current.get("review_note", ""), height=100)
    if st.form_submit_button("Save review", type="primary", use_container_width=True):
        override_value = correct_verdict if correct_verdict != "—" else ""
        findings_store.update_review(
            sel_url, new_status, new_note,
            correct_verdict=override_value)
        st.success("Saved.")
        st.rerun()

# ── Export ────────────────────────────────────────────────────────────────────
st.markdown("---")
if st.button("Export findings to CSV"):
    p = findings_store.export_csv()
    st.success(f"Exported to `{p}`")
