"""Findings Table — portfolio view of all apps with verdicts, risk indicators, and review status."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import json
import streamlit as st
import pandas as pd
from uw_app.resolve import load_apps_index_merged
from uw_app.uw_cache import get_uw_for_app
from uw_app.review_store import get_all_reviews, set_review, VALID_STATUSES

st.set_page_config(page_title="UW Findings | UW Lookup", layout="wide", initial_sidebar_state="auto")

# ── Load data ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=120)
def _build_table():
    by_app_id, _, _, _ = load_apps_index_merged()
    reviews = get_all_reviews()
    rows = []
    for app_id, rec in by_app_id.items():
        uw = get_uw_for_app(app_id) or {}
        rev = reviews.get(app_id, {})
        verdict = (uw.get("verdict") or "").strip()
        reasoning = (uw.get("reasoning") or "").strip()
        non_comp = (uw.get("non_compliant_subcategories") or "").strip()
        categories = rec.get("categories") or ""
        if isinstance(categories, list):
            categories = ", ".join(str(c) for c in categories)
        elif isinstance(categories, str):
            try:
                parsed = json.loads(categories)
                if isinstance(parsed, list):
                    categories = ", ".join(str(c) for c in parsed)
            except Exception:
                pass
        rows.append({
            "app_id": app_id,
            "App Name": rec.get("app_name") or "—",
            "App URL": rec.get("app_url") or "",
            "Verdict": verdict or "Not run",
            "Reasoning": reasoning[:200] + ("…" if len(reasoning) > 200 else ""),
            "Non-compliant": non_comp[:150] + ("…" if len(non_comp) > 150 else ""),
            "Categories": categories,
            "User Description": (rec.get("user_description") or "")[:150],
            "Review Status": rev.get("status", "Pending"),
            "Analyst Note": rev.get("note", ""),
            "Review Updated": rev.get("updated_at", ""),
        })
    return pd.DataFrame(rows)


def _color_verdict(val):
    v = (val or "").lower()
    if v == "allowed":
        return "background-color: #d4edda; color: #155724"
    if v in ("restricted", "not-allowed", "not allowed"):
        return "background-color: #f8d7da; color: #721c24"
    if "manual review" in v:
        return "background-color: #fff3cd; color: #856404"
    if v == "not run":
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


# ── KPIs ───────────────────────────────────────────────────────────────────────

df = _build_table()
total = len(df)

st.title("Findings Table")
st.caption("All apps with UW verdicts, risk indicators, and review workflow.")

col_k1, col_k2, col_k3, col_k4, col_k5 = st.columns(5)
with col_k1:
    st.metric("Total Apps", total)
with col_k2:
    run_count = len(df[df["Verdict"] != "Not run"])
    st.metric("UW Complete", f"{run_count}/{total}", delta=f"{run_count/total*100:.0f}%" if total else "0%")
with col_k3:
    reviewed_count = len(df[df["Review Status"].isin(["Reviewed", "Approved", "Declined"])])
    st.metric("Reviewed", f"{reviewed_count}/{total}", delta=f"{reviewed_count/total*100:.0f}%" if total else "0%")
with col_k4:
    flagged = len(df[df["Verdict"].str.lower().isin(["restricted", "not-allowed", "not allowed"])])
    st.metric("Flagged", flagged, delta_color="inverse" if flagged > 0 else "off")
with col_k5:
    manual = len(df[df["Verdict"].str.lower().str.contains("manual review", na=False)])
    st.metric("Manual Review", manual)

st.markdown("---")

# ── Filters ────────────────────────────────────────────────────────────────────

col_f1, col_f2, col_f3, col_f4 = st.columns(4)
with col_f1:
    verdict_opts = ["All"] + sorted(df["Verdict"].unique().tolist())
    verdict_filter = st.selectbox("Verdict", verdict_opts, index=0)
with col_f2:
    status_opts = ["All"] + sorted(df["Review Status"].unique().tolist())
    status_filter = st.selectbox("Review Status", status_opts, index=0)
with col_f3:
    search_text = st.text_input("Search (name, URL, description)", placeholder="Type to filter…")
with col_f4:
    sort_col = st.selectbox("Sort by", ["App Name", "Verdict", "Review Status", "Categories"], index=0)

filtered = df.copy()
if verdict_filter != "All":
    filtered = filtered[filtered["Verdict"] == verdict_filter]
if status_filter != "All":
    filtered = filtered[filtered["Review Status"] == status_filter]
if search_text.strip():
    q = search_text.strip().lower()
    mask = (
        filtered["App Name"].str.lower().str.contains(q, na=False)
        | filtered["App URL"].str.lower().str.contains(q, na=False)
        | filtered["User Description"].str.lower().str.contains(q, na=False)
        | filtered["app_id"].str.lower().str.contains(q, na=False)
    )
    filtered = filtered[mask]
filtered = filtered.sort_values(sort_col, key=lambda s: s.str.lower() if s.dtype == "object" else s)

st.caption(f"Showing {len(filtered)} of {total} apps")

# ── Verdict distribution chart ─────────────────────────────────────────────────

with st.expander("Verdict distribution", expanded=False):
    vc = df["Verdict"].value_counts()
    st.bar_chart(vc)

# ── Table ──────────────────────────────────────────────────────────────────────

display_cols = ["app_id", "App Name", "Verdict", "Review Status", "Analyst Note",
                "Categories", "User Description", "Reasoning", "Non-compliant"]

styled = (
    filtered[display_cols]
    .style
    .map(_color_verdict, subset=["Verdict"])
    .map(_color_review, subset=["Review Status"])
)
st.dataframe(
    styled,
    use_container_width=True,
    height=min(800, 80 + 35 * len(filtered)),
    column_config={
        "app_id": st.column_config.TextColumn("App ID", width="small"),
        "App Name": st.column_config.TextColumn("App Name", width="medium"),
        "Verdict": st.column_config.TextColumn("Verdict", width="small"),
        "Review Status": st.column_config.TextColumn("Status", width="small"),
        "Analyst Note": st.column_config.TextColumn("Note", width="medium"),
        "Categories": st.column_config.TextColumn("Categories", width="medium"),
        "User Description": st.column_config.TextColumn("Description", width="large"),
        "Reasoning": st.column_config.TextColumn("Reasoning", width="large"),
        "Non-compliant": st.column_config.TextColumn("Non-compliant", width="medium"),
    },
    hide_index=True,
)

# ── Review / Notes editor ──────────────────────────────────────────────────────

st.markdown("---")
st.subheader("Update Review Status & Notes")
st.caption("Select an app and set its review status + analyst note. Changes are saved immediately.")

col_r1, col_r2 = st.columns([1, 2])
with col_r1:
    app_options = [(r["app_id"], r["App Name"]) for _, r in filtered.iterrows()]
    app_labels = [f"{name}  ({aid[:12]}…)" for aid, name in app_options]
    if app_labels:
        selected_idx = st.selectbox("App", range(len(app_labels)), format_func=lambda i: app_labels[i])
        selected_app_id = app_options[selected_idx][0]
    else:
        st.info("No apps match the current filter.")
        selected_app_id = None

if selected_app_id:
    reviews = get_all_reviews()
    current = reviews.get(selected_app_id, {})
    with col_r2:
        with st.form("review_form", clear_on_submit=False):
            new_status = st.selectbox(
                "Status",
                VALID_STATUSES,
                index=VALID_STATUSES.index(current.get("status", "Pending")) if current.get("status", "Pending") in VALID_STATUSES else 0,
            )
            new_note = st.text_area("Analyst note", value=current.get("note", ""), height=100)
            if st.form_submit_button("Save", type="primary"):
                set_review(selected_app_id, new_status, new_note)
                st.success(f"Saved review for {selected_app_id[:12]}…")
                _build_table.clear()
                st.rerun()
