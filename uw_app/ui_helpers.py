"""
Shared UI helpers for all Streamlit screener pages.
Single source of truth for icons, table building, KPIs, CSS, and filters.
"""
from __future__ import annotations

import streamlit as st
import pandas as pd
from uw_app import findings_store

REVIEW_STATUSES = ("Pending", "Reviewed", "Escalated", "Approved", "Declined")

SCREENER_CSS = """<style>
    .verdict-badge { display:inline-block; padding:6px 16px; border-radius:6px;
                     font-weight:bold; font-size:14px; color:white; }
    .verdict-red { background:#dc3545; }
    .verdict-orange { background:#fd7e14; }
    .verdict-green { background:#28a745; }
    .verdict-gray { background:#6c757d; }
    .kpi-box { text-align:center; padding:12px; border-radius:8px; background:#f8f9fa; }
    .kpi-num { font-size:28px; font-weight:bold; }
    .kpi-label { font-size:12px; color:#666; }
</style>"""


def verdict_icon(v: str) -> str:
    v = (v or "").lower()
    if "not supportable" in v:
        return "🔴"
    if "not enabled" in v or "restricted" in v:
        return "🟡"
    if "supportable" in v:
        return "🟢"
    return "⚪"


def review_icon(v: str) -> str:
    v = (v or "").lower()
    if v == "approved":
        return "✅"
    if v == "declined":
        return "❌"
    if v == "escalated":
        return "⚠️"
    if v == "reviewed":
        return "🔵"
    return ""


def render_kpis(all_findings: list[dict]) -> None:
    """Render the standard 5-column KPI bar."""
    total = len(all_findings)
    red_count = sum(1 for f in all_findings if f.get("overall_color") == "red")
    orange_count = sum(1 for f in all_findings if f.get("overall_color") == "orange")
    green_count = sum(1 for f in all_findings if f.get("overall_color") == "green")
    reviewed = sum(1 for f in all_findings
                   if f.get("review_status") in ("Reviewed", "Approved", "Declined"))

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num">{total}</div>'
                    f'<div class="kpi-label">Total Screened</div></div>',
                    unsafe_allow_html=True)
    with k2:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#dc3545">'
                    f'{red_count}</div><div class="kpi-label">Not Supportable</div></div>',
                    unsafe_allow_html=True)
    with k3:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#fd7e14">'
                    f'{orange_count}</div><div class="kpi-label">Restricted / Review</div></div>',
                    unsafe_allow_html=True)
    with k4:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num" style="color:#28a745">'
                    f'{green_count}</div><div class="kpi-label">Likely Supportable</div></div>',
                    unsafe_allow_html=True)
    with k5:
        st.markdown(f'<div class="kpi-box"><div class="kpi-num">{reviewed}/{total}</div>'
                    f'<div class="kpi-label">Reviewed</div></div>',
                    unsafe_allow_html=True)


def render_filters(all_findings: list[dict],
                   key_prefix: str = "ft") -> list[dict]:
    """Render verdict / review-status / search filters. Returns filtered list."""
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        verdict_options = ["All"] + sorted(
            set(f.get("overall_verdict", "") for f in all_findings))
        verdict_filter = st.selectbox("Verdict", verdict_options,
                                      key=f"{key_prefix}_verdict")
    with fc2:
        review_options = ["All"] + list(REVIEW_STATUSES)
        review_filter = st.selectbox("Review Status", review_options,
                                     key=f"{key_prefix}_review")
    with fc3:
        search = st.text_input("Search",
                                placeholder="app name, URL, description…",
                                key=f"{key_prefix}_search")

    filtered = all_findings
    if verdict_filter != "All":
        filtered = [f for f in filtered
                    if f.get("overall_verdict") == verdict_filter]
    if review_filter != "All":
        filtered = [f for f in filtered
                    if f.get("review_status", "Pending") == review_filter]
    if search.strip():
        q = search.strip().lower()
        filtered = [f for f in filtered if
                    q in (f.get("url") or "").lower() or
                    q in (f.get("app_name") or "").lower() or
                    q in (f.get("app_description") or "").lower() or
                    q in (f.get("app_id") or "").lower() or
                    q in (f.get("top_category") or "").lower() or
                    q in (f.get("top_p_and_r_name") or "").lower()]
    return filtered


_REVIEW_CATEGORY = pd.CategoricalDtype(categories=list(REVIEW_STATUSES), ordered=True)


def build_findings_df(findings: list[dict]) -> tuple[pd.DataFrame, list[str]]:
    """Build the standard findings DataFrame + parallel url list.
    Review column is a pd.Categorical so data_editor renders a dropdown."""
    sorted_rows = findings_store.sort_findings(findings)
    rows = []
    url_list: list[str] = []
    for f in sorted_rows:
        pr_name = (f.get("top_p_and_r_name")
                   or (f"P&R #{f.get('top_p_and_r_id')}"
                       if f.get("top_p_and_r_id") else "—"))
        app_url = f.get("url", "")
        vrd = f.get("overall_verdict", "")
        review_st = f.get("review_status", "Pending")
        url_list.append(app_url)
        rows.append({
            "URL": app_url,
            "Name": f.get("app_name") or "—",
            "Verdict": f"{verdict_icon(vrd)} {vrd}",
            "Conf": f.get("confidence", 0),
            "P&R Index": pr_name,
            "Stripe Category": f.get("top_category", "—"),
            "Subcategory": f.get("top_subcategory", "—"),
            "Description": (f.get("app_description") or "")[:120],
            "Review": review_st,
            "Note": f.get("review_note", ""),
            "When": f.get("screened_at", ""),
        })
    df = pd.DataFrame(rows)
    df["Review"] = df["Review"].astype(_REVIEW_CATEGORY)
    return df, url_list


FINDINGS_COLUMN_CONFIG = {
    "URL": st.column_config.TextColumn("URL", width="medium"),
    "Name": st.column_config.TextColumn("Name", width="small"),
    "Verdict": st.column_config.TextColumn("Verdict", width="medium"),
    "Conf": st.column_config.ProgressColumn("Conf", min_value=0,
                                             max_value=100, format="%d%%"),
    "P&R Index": st.column_config.TextColumn("P&R Index", width="large"),
    "Stripe Category": st.column_config.TextColumn("Stripe Category",
                                                     width="medium"),
    "Subcategory": st.column_config.TextColumn("Subcategory", width="medium"),
    "Description": st.column_config.TextColumn("Description", width="large"),
    "Review": st.column_config.SelectboxColumn(
        "Review", width="small",
        options=list(REVIEW_STATUSES),
        required=True,
    ),
    "Note": st.column_config.TextColumn("Note", width="medium"),
    "When": st.column_config.TextColumn("When", width="small"),
}

_DISABLED_COLS = ["URL", "Name", "Verdict", "Conf", "P&R Index",
                  "Stripe Category", "Subcategory", "Description", "When"]


def render_findings_table(df: pd.DataFrame, url_list: list[str], *,
                          key: str = "findings_table") -> None:
    """Render editable findings table. Review dropdown persists changes immediately."""
    edited_df = st.data_editor(
        df,
        width="stretch",
        height=min(700, 60 + 35 * len(df)),
        column_config=FINDINGS_COLUMN_CONFIG,
        disabled=_DISABLED_COLS,
        hide_index=True,
        num_rows="fixed",
        key=key,
    )
    for idx in range(len(df)):
        old_review = str(df.at[idx, "Review"])
        old_note = str(df.at[idx, "Note"])
        new_review = str(edited_df.at[idx, "Review"])
        new_note = str(edited_df.at[idx, "Note"])
        if new_review != old_review or new_note != old_note:
            url = url_list[idx]
            findings_store.update_review(url, new_review, new_note)


def _fmt_list(items: list | None, limit: int = 8) -> str:
    if not items:
        return ""
    return ", ".join(str(x) for x in items[:limit])


def render_findings_rows(findings: list[dict], *, key_prefix: str = "fr") -> None:
    """Render each finding as a compact row with a per-row expander for details + inline review."""
    sorted_rows = findings_store.sort_findings(findings)
    for i, f in enumerate(sorted_rows):
        vrd = f.get("overall_verdict", "")
        icon = verdict_icon(vrd)
        name = f.get("app_name") or "—"
        url = f.get("url", "")
        conf = f.get("confidence", 0)
        pr = f.get("top_p_and_r_name") or "—"
        review_st = f.get("review_status", "Pending")
        r_icon = review_icon(review_st)
        stripe_cat = f.get("top_category", "—")
        sub = f.get("top_subcategory", "—")

        # ── Compact summary row ──
        header = (f"{icon} **{name}** — {vrd} ({conf}%) — "
                  f"{stripe_cat} › {sub} — {r_icon} {review_st}")

        with st.expander(header, expanded=False):
            # Top: URL + verdict badge + review dropdown
            top1, top2, top3 = st.columns([3, 1, 1])
            with top1:
                st.markdown(f"**URL:** [{url}]({url})")
                app_id = f.get("app_id")
                if app_id:
                    st.caption(f"App ID: `{app_id}`")
            with top2:
                color_map = {"red": "#dc3545", "orange": "#fd7e14",
                             "green": "#28a745", "gray": "#6c757d"}
                bg = color_map.get(f.get("overall_color", "gray"), "#6c757d")
                st.markdown(
                    f'<div style="text-align:center;padding:6px 12px;border-radius:6px;'
                    f'background:{bg};color:white;font-weight:bold;font-size:13px;">'
                    f'{vrd}<br>{conf}%</div>',
                    unsafe_allow_html=True)
            with top3:
                current_idx = (list(REVIEW_STATUSES).index(review_st)
                               if review_st in REVIEW_STATUSES else 0)
                new_status = st.selectbox(
                    "Review", REVIEW_STATUSES,
                    index=current_idx,
                    key=f"{key_prefix}_review_{i}",
                    label_visibility="collapsed",
                )
                if new_status != review_st:
                    findings_store.update_review(url, new_status,
                                                 f.get("review_note", ""))
                    st.rerun()

            # ── App intent & description ──
            desc = f.get("app_description") or ""
            meta_title = f.get("meta_title") or ""
            meta_desc = f.get("meta_description") or ""
            if desc or meta_title or meta_desc:
                st.markdown("##### What is this app?")
                if meta_title:
                    st.markdown(f"**Title:** {meta_title}")
                if desc:
                    st.markdown(f"**App description:** {desc[:400]}")
                elif meta_desc:
                    st.markdown(f"**Meta description:** {meta_desc[:400]}")

            # ── Classification info ──
            ic1, ic2, ic3 = st.columns(3)
            with ic1:
                st.markdown(f"**P&R Index:** {pr}")
            with ic2:
                st.markdown(f"**Stripe:** {stripe_cat}")
            with ic3:
                st.markdown(f"**Sub:** {sub}")

            # ── Why it was flagged ──
            matches = f.get("policy_matches", [])
            if matches:
                st.markdown("##### Why was it flagged?")
                for m in matches[:8]:
                    sev = "🔴" if m.get("color") == "red" else (
                        "🟡" if m.get("color") == "orange" else "🟢")
                    kws = m.get("keywords", [])
                    m_pr = (m.get("p_and_r_name")
                            or (f"P&R #{m.get('p_and_r_id', '?')}"
                                if m.get("p_and_r_id") else None))
                    stripe = f"{m.get('category', '')} › {m.get('subcategory', '')}"
                    label = f"**{m_pr}** → {stripe}" if m_pr else f"**{stripe}**"
                    st.markdown(
                        f"{sev} {label} — *{m.get('verdict', '')}* "
                        f"({m.get('confidence', 0)}%)")
                    if kws:
                        st.markdown(
                            f"&nbsp;&nbsp;&nbsp;&nbsp;Matched keywords: "
                            f"`{', '.join(kws[:8])}`")

            # ── What can a shopper buy ──
            pay = f.get("payment_signals") or []
            entities = f.get("entity_types") or []
            if pay or entities:
                st.markdown("##### What can a shopper buy?")
                if pay:
                    st.markdown(f"**Payment signals:** {_fmt_list(pay)}")
                if entities:
                    st.markdown(f"**Data entities:** {_fmt_list(entities)}")

            # ── Retrieved data & signals ──
            signals: list[tuple[str, str]] = []
            login = f.get("login_methods") or []
            if login:
                signals.append(("Auth / Login", _fmt_list(login)))
            feats = f.get("features") or []
            if feats:
                signals.append(("Features", _fmt_list(feats)))
            integ = f.get("integrations") or []
            if integ:
                signals.append(("Integrations", _fmt_list(integ)))
            vis = f.get("visibility")
            if vis:
                signals.append(("Visibility", str(vis)))
            lr = f.get("login_required")
            if lr is not None:
                signals.append(("Login required", "Yes" if lr else "No"))
            sources = f.get("data_sources") or []
            if sources:
                signals.append(("Data sources", _fmt_list(sources)))
            clen = f.get("content_length") or 0
            if clen:
                signals.append(("Content scraped", f"{clen:,} chars"))
            elapsed = f.get("elapsed_seconds")
            if elapsed:
                signals.append(("Scrape time", f"{elapsed}s"))

            if signals:
                st.markdown("##### Retrieved data & signals")
                for label, val in signals:
                    st.markdown(f"**{label}:** {val}")

            # ── Scraped page content ──
            summary = f.get("page_content_summary") or ""
            if summary:
                st.markdown("##### Scraped page content")
                st.code(summary[:3000], language=None)

            # ── Analyst note (editable) ──
            existing_note = f.get("review_note", "")
            new_note = st.text_input(
                "Analyst note", value=existing_note,
                key=f"{key_prefix}_note_{i}",
                placeholder="Add a note…",
            )
            if new_note != existing_note:
                findings_store.update_review(
                    url, f.get("review_status", "Pending"), new_note)

            screened = f.get("screened_at", "")
            if screened:
                st.caption(f"Screened: {screened}")


def render_policy_matches(matches: list[dict], *, expanded: bool | None = None) -> None:
    """Render policy-match expander used by result cards and review page."""
    if not matches:
        return
    if expanded is None:
        expanded = len(matches) <= 3
    match_lines = []
    for m in matches[:5]:
        sev = "🔴" if m.get("color") == "red" else ("🟡" if m.get("color") == "orange" else "🟢")
        kws = ", ".join(m.get("keywords", [])[:4])
        pr = (m.get("p_and_r_name")
              or (f"P&R #{m.get('p_and_r_id', '?')}" if m.get("p_and_r_id") else None))
        stripe_part = f"{m.get('category', '')} › {m.get('subcategory', '')}"
        sig_ids = m.get("signal_ids", [])
        reg = m.get("regulation", "")
        extras = ""
        if sig_ids:
            extras += f"  Signals: `{', '.join(sig_ids)}`"
        if reg:
            extras += f"  Reg: _{reg}_"
        if pr:
            match_lines.append(
                f"{sev} **{pr}** → {stripe_part} — "
                f"*{m.get('verdict', '')}* ({m.get('confidence', 0)}%)  `{kws}`{extras}")
        else:
            match_lines.append(
                f"{sev} **{stripe_part}** — "
                f"*{m.get('verdict', '')}* ({m.get('confidence', 0)}%)  `{kws}`{extras}")
    with st.expander(f"Policy matches ({len(matches)})", expanded=expanded):
        st.markdown("  \n".join(match_lines))
