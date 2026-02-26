"""
UW Internal App — desktop version. Run on your laptop (no SSO).
  Run from project folder:  python3 run_uw_app.py
"""
import sys
from pathlib import Path

# Ensure project root is on path so "uw_app" can be imported when run as a script.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import streamlit as st
from uw_app.resolve import resolve, DEFAULT_APPS_JSON
from uw_app.uw_cache import get_uw_for_app
from uw_app.profile import profile_from_app_record, profile_from_trino_row
from uw_app.trino_client import get_full_profile, is_configured as trino_configured

st.set_page_config(page_title="UW Lookup on WP base44 users", layout="wide", initial_sidebar_state="collapsed")

# --- Header ---
st.title("UW Lookup on WP base44 users")
st.markdown("Look up any app by **App ID**, **MSID**, or **WixPayments account ID** to see its profile and policy check.")
st.markdown("---")

# --- Search ---
col1, col2 = st.columns([1, 3])
with col1:
    id_type = st.selectbox(
        "I have",
        options=["app_id", "msid", "wp_account_id"],
        format_func=lambda x: {
            "app_id": "App ID (24-character)",
            "msid": "MSID",
            "wp_account_id": "WixPayments account ID",
        }[x],
        index=0,
    )
with col2:
    value = st.text_input(
        "Paste the value here",
        placeholder="e.g. 698406273ade17b9bd851188  or  c6ab1a9b-1830-4f53-a221-5d2ae0597796",
        label_visibility="collapsed",
    )

st.caption("App ID is a 24-character hex; MSID and WP account ID are UUIDs (with or without hyphens).")

def _stub_app_list():
    if not DEFAULT_APPS_JSON.exists():
        return []
    try:
        rows = json.loads(DEFAULT_APPS_JSON.read_text(encoding="utf-8"))
        return [(r.get("app_id"), r.get("app_name") or "—") for r in rows if r.get("app_id")]
    except Exception:
        return []

if trino_configured():
    st.caption("✅ Using **live data** from Trino — any app can be looked up.")
else:
    stub_apps = _stub_app_list()
    st.caption("📁 Using **sample data** — only the apps below can be looked up. For any app, set **TRINO_HOST** and restart.")
    with st.expander("Sample App IDs that work (click to copy)", expanded=False):
        for aid, name in stub_apps:
            st.code(aid, language=None)
            st.caption(name)
        st.caption("Paste one of the App IDs above into the search box and click Look up.")

if st.button("Look up", type="primary"):
    if not (value and value.strip()):
        st.warning("Please paste or type a value above.")
    else:
        lookup_error = None
        try:
            with st.spinner("Looking up…"):
                app_record = resolve(id_type, value.strip())
        except Exception as e:
            lookup_error = e
            app_record = None
        if lookup_error:
            st.error("Lookup failed. Details: " + str(lookup_error))
            with st.expander("Technical details (for debugging)"):
                import traceback
                st.code(traceback.format_exc())
        elif not app_record:
            if trino_configured():
                st.error("We couldn't find an app for that. Double-check the value and that you selected the right type (App ID / MSID / WP account ID).")
            else:
                stub_apps = _stub_app_list()
                st.error("That app isn't in the sample set, so it can't be found in sample mode.")
                st.info("**To try the app:** Use one of the sample App IDs from the expander above (e.g. `698406273ade17b9bd851188` for Risky Recall). **To look up any app:** set TRINO_HOST and restart the app for live data.")
                with st.expander("Sample App IDs that work"):
                    for aid, name in stub_apps:
                        st.code(aid, language=None)
                        st.caption(name)
        else:
            app_id = app_record.get("app_id")
            app_name = app_record.get("app_name") or "Unnamed app"
            st.markdown("---")
            st.subheader(f"📱 {app_name}")
            st.caption(f"App ID: `{app_id}`")

            # --- App profile ---
            if app_id and trino_configured():
                with st.spinner("Loading full profile…"):
                    profile_row = get_full_profile(app_id)
                profile_rows = profile_from_trino_row(profile_row) if profile_row else profile_from_app_record(app_record)
            else:
                profile_rows = profile_from_app_record(app_record)

            st.subheader("📋 App profile")
            if profile_rows:
                st.dataframe(profile_rows, width="stretch", hide_index=True, column_config={"field": "Field", "value": "Value"})
            else:
                st.info("No profile fields available.")

            # --- UW check ---
            st.subheader("📌 UW check vs policy")
            uw = get_uw_for_app(app_id) if app_id else None
            if uw:
                verdict = (uw.get("verdict") or "—").strip()
                if verdict.lower() == "allowed":
                    st.success(f"**Verdict: {verdict}**")
                elif verdict.lower() in ("restricted", "not-allowed", "not allowed"):
                    st.warning(f"**Verdict: {verdict}**")
                else:
                    st.info(f"**Verdict: {verdict}**")
                if uw.get("reasoning"):
                    st.markdown("**Why:** " + (uw.get("reasoning") or ""))
                if uw.get("non_compliant_subcategories"):
                    st.markdown("**Non-compliant subcategories:** " + (uw.get("non_compliant_subcategories") or ""))
                if uw.get("app_summary"):
                    with st.expander("📄 App summary (what the app does / sells)"):
                        st.markdown(uw.get("app_summary"))
                if uw.get("step1_what_sold"):
                    with st.expander("🛒 What is sold (Step 1)"):
                        st.markdown(uw.get("step1_what_sold"))
                if uw.get("step2_comparison"):
                    with st.expander("📜 Policy comparison (Step 2)"):
                        st.markdown(uw.get("step2_comparison"))
            else:
                st.info("No underwriting result cached for this app yet. Run the pipeline for this app to generate a verdict.")
