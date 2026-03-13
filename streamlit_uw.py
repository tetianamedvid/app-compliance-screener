# UW Lookup app — run this file from the project folder. No package tricks.
import sys
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass
print("UW Lookup: loading…", flush=True)
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
# Load .env so TRINO_HOST is set before any uw_app import (production = look up any app)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import json
import os
import subprocess
import streamlit as st
from uw_app.resolve import resolve, get_apps_json_path, load_apps_index_merged, add_app_to_user_list
from uw_app import data_refresh
from uw_app.uw_cache import get_uw_for_app
from uw_app.profile import profile_from_app_record, profile_from_trino_row
from uw_app.trino_client import get_full_profile, get_conversation_snapshots, get_conversation_messages, is_configured as trino_configured, test_connection, get_last_trino_error
from run_underwriting import run_standalone_uw
from uw_app.scraper import _get_or_create_browser


def _load_full_profiles_json():
    """Load optional full-profiles JSON (keyed by app_id) for extended profile + conversation when Trino is unavailable."""
    raw = os.environ.get("FULL_PROFILES_JSON_PATH", "").strip()
    p = Path(raw) if raw and Path(raw).is_absolute() else (ROOT / (raw or "data/full_profiles.json"))
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and all(isinstance(k, str) for k in data.keys()):
            return data
        if isinstance(data, list):
            return {str(r.get("app_id")): r for r in data if r.get("app_id")}
    except Exception:
        pass
    return {}

st.set_page_config(page_title="UW Lookup on WP base44 users", layout="wide", initial_sidebar_state="auto")

# Pre-warm Playwright browser on startup (runs once, reused for all subsequent scrapes)
if "browser_warmed" not in st.session_state:
    try:
        import threading
        threading.Thread(target=_get_or_create_browser, daemon=True).start()
        st.session_state["browser_warmed"] = True
    except Exception:
        pass

# Quiet refresh: if APPS_REFRESH_SOURCE_PATH is set, re-import from that file at most once per hour (or on first open)
data_refresh.run_refresh_if_due()

# One-time SSO check when Trino is configured: opens browser for login if needed
if "trino_sso_checked" not in st.session_state and trino_configured():
    st.session_state["trino_sso_checked"] = True
    with st.spinner("Connecting to Trino (SSO)… If a browser opens, log in to enable live data."):
        st.session_state["trino_live"] = test_connection()
else:
    st.session_state.setdefault("trino_live", trino_configured() and st.session_state.get("trino_sso_checked", False))
st.title("UW Lookup on WP base44 users")
st.markdown("Look up any app by **App ID**, **MSID**, or **WixPayments account ID** to see its profile and policy check.")
if not (trino_configured() and st.session_state.get("trino_live")):
    st.success("**App is ready.** Use an App ID from the list below and click **Look up**.")
st.markdown("---")

# Standalone APP URL check — at top so users can scrape any URL without looking up first
st.subheader("🔗 Standalone APP URL check")
st.caption("Scrape any app URL and run UW check (no app in list required).")
_default = st.session_state.get("standalone_result_url") or st.session_state.get("standalone_prefill_url", "")
with st.form("standalone_uw_form", clear_on_submit=False):
    standalone_url = st.text_input(
        "App URL",
        value=_default,
        placeholder="e.g. https://my-app.base44.app",
        key="standalone_url_input",
        label_visibility="collapsed",
    )
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        scrape_deep = st.checkbox("Deep scrape (JS render + links)", value=False, key="standalone_deep_v2", help="Off = fast (~1-3s). On = launches browser (~5-15s).")
    with col_s2:
        llm_mode = st.selectbox(
            "Analysis",
            options=["none", "auto", "openai", "ollama"],
            format_func=lambda x: {"none": "Fast (no LLM)", "auto": "Auto (LLM — slower)", "openai": "OpenAI", "ollama": "Ollama"}[x],
            index=0,
            key="standalone_llm_mode",
            help="Auto tries OpenAI then Ollama. Rule-based: full extraction without any API key.",
        )
    submitted = st.form_submit_button("Scrape & Run UW")
if submitted:
    url_to_check = (standalone_url or "").strip()
    if url_to_check and not url_to_check.startswith("http"):
        url_to_check = "https://" + url_to_check
    if url_to_check.startswith("http"):
        try:
            spinner_msg = "Deep analysis (scrape + forensics + screenshot)…" if scrape_deep else "Analyzing (scrape + forensics + risk score)…"
            if llm_mode != "none":
                spinner_msg += " + LLM"
            with st.spinner(spinner_msg):
                result = run_standalone_uw(url_to_check, llm_mode=llm_mode, scrape_deep=scrape_deep)
            st.session_state["standalone_result"] = result
            st.session_state["standalone_result_url"] = url_to_check
            if "ask_question_answer" in st.session_state:
                del st.session_state["ask_question_answer"]
            if "ask_question_question" in st.session_state:
                del st.session_state["ask_question_question"]
            st.rerun()
        except Exception as e:
            st.error("Scrape & UW failed: " + str(e))
            import traceback
            with st.expander("Details"):
                st.code(traceback.format_exc())
    else:
        st.warning("Enter a valid URL (e.g. https://my-app.base44.app).")

if st.session_state.get("standalone_result"):
    res = st.session_state["standalone_result"]
    if res.get("error"):
        st.error(res["error"])
    else:
        st.success("**Scraped & UW check complete.**")
        base44_app_id = res.get("base44_app_id")
        base44_app_name = res.get("base44_app_name")
        if base44_app_id or base44_app_name:
            st.info(
                f"**App ID (from Base44 API):** `{base44_app_id or '—'}`  \n"
                f"**App name (from Base44 API):** {base44_app_name or '—'}"
            )

        # --- Risk Score Badge ---
        risk_score = res.get("risk_score")
        risk_color = res.get("risk_color", "gray")
        risk_verdict = res.get("risk_verdict", "—")
        risk_category = res.get("risk_category", "Unknown")
        color_map = {"green": "#28a745", "orange": "#fd7e14", "red": "#dc3545", "gray": "#6c757d"}
        badge_hex = color_map.get(risk_color, "#6c757d")
        bg_map = {"green": "#d4edda", "orange": "#fff3cd", "red": "#f8d7da", "gray": "#e2e3e5"}
        bg_hex = bg_map.get(risk_color, "#e2e3e5")

        if risk_score is not None:
            st.markdown(
                f'<div style="background:{bg_hex};border-left:5px solid {badge_hex};padding:16px 20px;border-radius:8px;margin:12px 0">'
                f'<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">'
                f'<span style="background:{badge_hex};color:white;font-size:28px;font-weight:bold;padding:8px 18px;border-radius:50%;min-width:50px;text-align:center">{risk_score}</span>'
                f'<div>'
                f'<div style="font-size:20px;font-weight:bold;color:{badge_hex}">{risk_verdict}</div>'
                f'<div style="font-size:14px;color:#555">Category: {risk_category}'
                f'{" &nbsp;|&nbsp; Payments detected" if res.get("has_payments") else ""}</div>'
                f'</div></div></div>',
                unsafe_allow_html=True,
            )

        # --- Risk Flags ---
        risk_flags = res.get("risk_flags") or []
        if risk_flags:
            with st.expander("Risk signals & flags", expanded=True):
                for flag in risk_flags:
                    if flag.startswith("High-risk") or flag.startswith("Crypto miner") or flag.startswith("Payment bypass"):
                        st.markdown(f"- :red[**{flag}**]")
                    elif flag.startswith("Medium-risk") or flag.startswith("Auth:") or "obfusc" in flag.lower():
                        st.markdown(f"- :orange[{flag}]")
                    else:
                        st.markdown(f"- {flag}")

        # --- JS Security Forensics ---
        js_sec = res.get("js_security") or {}
        has_forensics = any(js_sec.get(k) for k in ("payment_bypass", "crypto", "miners"))
        if has_forensics or js_sec.get("obfuscation"):
            with st.expander("JS Forensics", expanded=has_forensics):
                if js_sec.get("payment_bypass"):
                    st.markdown("**Payment bypass signals:**")
                    for s in js_sec["payment_bypass"]:
                        st.markdown(f"- :red[{s}]")
                if js_sec.get("crypto"):
                    st.markdown("**Crypto/Web3 signals:**")
                    for s in js_sec["crypto"]:
                        st.markdown(f"- :orange[{s}]")
                if js_sec.get("miners"):
                    st.markdown("**Crypto miners detected:**")
                    for s in js_sec["miners"]:
                        st.markdown(f"- :red[**{s}**]")
                if js_sec.get("obfuscation"):
                    st.markdown("- :orange[Heavily obfuscated JS code]")
                if not has_forensics and not js_sec.get("obfuscation"):
                    st.markdown("No suspicious JS signals found.")

        # --- Legal Pages ---
        legal = res.get("legal_info") or {}
        if legal:
            with st.expander("Legal page analysis"):
                if legal.get("pages_found"):
                    st.markdown("**Pages found:** " + ", ".join(legal["pages_found"]))
                    if legal.get("has_company_name"):
                        st.markdown("- Company/business name found")
                    if legal.get("has_address"):
                        st.markdown("- Physical address found")
                    if legal.get("has_contact"):
                        st.markdown("- Contact information found")
                else:
                    st.warning("No legal pages found (/privacy-policy, /terms, /legal, /about)")

        # --- LLM Assessment (if present) ---
        llm_a = res.get("llm_assessment")
        if llm_a:
            with st.expander("LLM risk assessment", expanded=True):
                st.json(llm_a)

        # --- Screenshot ---
        screenshot = res.get("screenshot_path")
        if screenshot:
            from pathlib import Path as _P
            if _P(screenshot).exists():
                with st.expander("Homepage screenshot"):
                    st.image(screenshot, use_container_width=True)

        # --- Traditional outputs ---
        with st.expander("Scraped content", expanded=False):
            scraped_text = res.get("scraped") or "(no content)"
            st.text(scraped_text[:8000] + ("…" if len(scraped_text or "") > 8000 else ""))

        policy_verdict = res.get("verdict") or "—"
        reasoning = res.get("reasoning") or ""
        no_llm = "No LLM available" in reasoning or "Manual Review Required" in policy_verdict
        with st.expander("App summary (middleman)", expanded=True):
            st.markdown(res.get("app_summary") or "")
        if not no_llm:
            with st.expander("Policy comparison"):
                st.markdown(res.get("policy_conclusion") or "")

        sources = res.get("sources_checked", "")
        if sources:
            st.caption(f"Sources: {sources}")

        st.markdown("---")
        st.subheader("Ask a question about this app")
        st.caption("Clarify policy concerns, e.g. \"Is this app selling 3D printed weapons?\" Requires OpenAI or Ollama.")
        with st.form("ask_question_form", clear_on_submit=False):
            q = st.text_area(
                "Your question",
                placeholder="e.g. Does this app sell 3D printed weapons or firearm parts?",
                key="ask_question_input",
                height=80,
            )
            q_submitted = st.form_submit_button("Get answer")
        if q_submitted and (q or "").strip():
            try:
                from run_underwriting import ask_question_about_app, load_policy
                policy_path = ROOT / "policy" / "policy-excerpt.txt"
                policy = load_policy(policy_path) if policy_path.exists() else None
                with st.spinner("Asking LLM…"):
                    answer = ask_question_about_app(
                        q.strip(),
                        res.get("scraped") or "",
                        res.get("app_summary") or "",
                        policy,
                    )
                st.session_state["ask_question_answer"] = answer
                st.session_state["ask_question_question"] = q.strip()
                st.rerun()
            except Exception as e:
                st.error("Failed: " + str(e))
        if st.session_state.get("ask_question_question") and st.session_state.get("ask_question_answer"):
            st.markdown(f"**Q:** {st.session_state['ask_question_question']}")
            st.markdown(f"**A:** {st.session_state['ask_question_answer']}")

st.markdown("---")

col1, col2 = st.columns([1, 3])
with col1:
    id_type = st.selectbox(
        "I have",
        options=["app_id", "msid", "wp_account_id", "app_url"],
        format_func=lambda x: {
            "app_id": "App ID (24-character)",
            "msid": "MSID",
            "wp_account_id": "WixPayments account ID",
            "app_url": "App URL",
        }[x],
        index=0,
    )
with col2:
    placeholder = (
        "e.g. https://my-app.base44.app"
        if id_type == "app_url"
        else "e.g. 698406273ade17b9bd851188"
    )
    value = st.text_input("Paste the value here", placeholder=placeholder, label_visibility="collapsed")
st.caption("App ID is 24-character hex; MSID and WP account ID are UUIDs; App URL is the full app URL (e.g. https://my-app.base44.app).")

def _stub_app_list():
    """App IDs and names from main list + user_apps.json (merged)."""
    try:
        by_app_id, _, _, _ = load_apps_index_merged()
        return [(aid, (r.get("app_name") or "—")) for aid, r in sorted(by_app_id.items())]
    except Exception:
        return []

stub_apps = _stub_app_list()
if trino_configured() and st.session_state.get("trino_live"):
    st.caption("✅ **Full data, unlimited:** live from Trino (SSO). Look up any app.")
else:
    # Local data: set APPS_JSON_PATH once; optional APPS_REFRESH_SOURCE_PATH = auto refresh every hour / on open
    c1, c2 = st.columns([3, 1])
    with c1:
        st.caption("📁 **Full data (local):** Set `APPS_JSON_PATH` in `.env` **once** to your JSON file. If that file is updated by a script or scheduled export, click **Reload app list** → no need to export manually every time.")
        last_ts = data_refresh.get_last_refresh_time()
        if last_ts > 0:
            from datetime import datetime
            st.caption(f"Data last refreshed: {datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M')} (auto-refresh when `APPS_REFRESH_SOURCE_PATH` is set).")
    with c2:
        if st.button("🔄 Reload app list", key="reload_list", help="Re-read the JSON file from disk (use after the file was updated)"):
            st.rerun()
    with st.expander("App IDs in your list (click to copy)"):
        for aid, name in stub_apps:
            st.code(aid, language=None)
            st.caption(name)
    # One-click update: upload export (CSV/JSON) → import into app list. You only QA.
    with st.expander("📤 Update app list from export (no scripts, no .env)", expanded=False):
        st.caption("Upload a CSV or JSON export from Quix/Trino (full app profile or chunk). The app will import it and use it as the app list. Then click **Reload app list** or just look up apps.")
        uploaded = st.file_uploader("Export file (CSV or JSON)", type=["csv", "json"], key="export_upload")
        if uploaded is not None:
            upload_dir = ROOT / "data" / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            suffix = ".csv" if (uploaded.name or "").lower().endswith(".csv") else ".json"
            upload_path = upload_dir / ("latest_export" + suffix)
            upload_path.write_bytes(uploaded.getvalue())
            ok, msg = data_refresh.import_upload_to_app_list(upload_path)
            if ok:
                st.success("✅ " + msg)
                st.rerun()
            else:
                st.error("Import failed: " + msg)
        st.caption("---")
        st.caption("**Or upload full_profiles.json** (extended profile + conversation per app_id):")
        fp_upload = st.file_uploader("full_profiles.json", type=["json"], key="fp_upload")
        if fp_upload is not None:
            try:
                data = json.loads(fp_upload.getvalue().decode("utf-8"))
                if isinstance(data, dict):
                    fp_path = ROOT / "data" / "full_profiles.json"
                    fp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    st.success(f"✅ Saved full_profiles.json with {len(data)} apps. Refresh to see.")
                    st.rerun()
                else:
                    st.error("JSON must be an object keyed by app_id.")
            except Exception as e:
                st.error("Import failed: " + str(e))

if st.button("Look up", type="primary"):
    if not (value and value.strip()):
        st.warning("Please paste or type a value above.")
    else:
        err = None
        try:
            with st.spinner("Looking up…"):
                app_record = resolve(id_type, value.strip())
        except Exception as e:
            err = e
            app_record = None
        if err:
            st.error("Lookup failed: " + str(err))
            with st.expander("Technical details"):
                import traceback
                st.code(traceback.format_exc())
        elif not app_record:
            trino_err = get_last_trino_error()
            raw = (value or "").strip()
            is_app_id = id_type == "app_id" and raw and 20 <= len(raw) <= 30 and raw.replace("-", "").replace("_", "").isalnum()
            is_url = id_type == "app_url" and raw and raw.startswith("http")
            if is_app_id:
                st.session_state["pending_add_app_id"] = raw
            if trino_configured() and not st.session_state.get("trino_live") and trino_err:
                st.warning("That app isn't in your local list. With Trino unavailable, you can only look up apps from the list above.")
                if is_app_id:
                    st.markdown("**To add this app and look it up:** use the **Add** button below (under this message).")
                else:
                    st.info("To add an app you must search by **App ID** (24-character). Switch 'I have' to App ID, paste the App ID, then click Look up.")
                with st.expander("Why Trino didn't run (for admins)"):
                    st.caption(trino_err)
            elif trino_configured() and trino_err:
                st.error("Trino connection or query failed, so live data didn't run. Error: " + trino_err)
                st.info("Check that TRINO_HOST in .env is correct (e.g. your Quix/Trino host). If you use SSO, a browser may need to open to log in on first use.")
            elif trino_configured():
                st.error("No app found. Check the value and type (App ID / MSID / WP account ID).")
            else:
                st.error("That app is not in the sample set.")
                if is_app_id:
                    st.session_state["pending_add_app_id"] = raw
            # Store URL for standalone pre-fill when app not found
            if is_url:
                st.session_state["standalone_prefill_url"] = raw
        else:
            if "pending_add_app_id" in st.session_state:
                del st.session_state["pending_add_app_id"]
            if "standalone_result" in st.session_state:
                del st.session_state["standalone_result"]
            if "standalone_result_url" in st.session_state:
                del st.session_state["standalone_result_url"]
            if "standalone_prefill_url" in st.session_state:
                del st.session_state["standalone_prefill_url"]
            app_id = app_record.get("app_id")
            app_name = app_record.get("app_name") or "Unnamed app"
            st.markdown("---")
            st.subheader(f"📱 {app_name}")
            st.caption(f"App ID: `{app_id}`")
            profile_row = None
            if app_id and trino_configured():
                with st.spinner("Loading full profile…"):
                    profile_row = get_full_profile(app_id)
            # When Trino has no full profile, merge optional full_profiles JSON (export with extended fields + conversation)
            record_for_profile = app_record
            full_profiles = _load_full_profiles_json()
            if not profile_row and app_id and app_id in full_profiles:
                record_for_profile = {**app_record, **{k: v for k, v in full_profiles[app_id].items() if k != "conversation_snapshots"}}
            # Only show instructions when profile is completely empty (no app_id, app_name, app_url)
            has_basic = bool((record_for_profile.get("app_id") or record_for_profile.get("app_name") or record_for_profile.get("app_url")))
            has_meta = bool((record_for_profile.get("user_description") or record_for_profile.get("categories") or record_for_profile.get("public_settings")))
            has_conv_data = bool(
                app_id and app_id in full_profiles and (
                    (isinstance(full_profiles[app_id].get("conversation_snapshots"), list) and len(full_profiles[app_id].get("conversation_snapshots") or []) > 0)
                    or (full_profiles[app_id].get("earliest_conversation_preview") or "").strip()
                    or (full_profiles[app_id].get("conversation_summary") or full_profiles[app_id].get("app_context_conversation_summary") or "").strip()
                )
            )
            trino_ok = trino_configured() and st.session_state.get("trino_live")
            # Don't show info box when we have basic profile; only when truly empty
            if not has_basic:
                st.info("No app data. Upload a JSON file with app records or set APPS_JSON_PATH in .env.")
            if profile_row:
                profile_rows = profile_from_trino_row(profile_row)
            else:
                profile_rows = profile_from_app_record(record_for_profile)
            st.subheader("📋 App profile")
            if profile_rows:
                st.dataframe(profile_rows, width="stretch", hide_index=True, column_config={"field": "Field", "value": "Value"})
                # When profile is minimal (local data, missing name/URL), point to full-data setup
                name_val = (app_record.get("app_name") or "").strip()
                url_val = (app_record.get("app_url") or "").strip()
                if (not trino_configured() or not st.session_state.get("trino_live")) and (name_val in ("", "—") or not url_val):
                    st.info("**Full data:** Use a JSON file with full records (app_id, app_name, app_url, msid, account_id, conversation_summary, etc.) and set `APPS_JSON_PATH` in `.env`. Restart the app — then every app in that file gets a full profile (unlimited apps). See docs/RUN-UW-APP-ON-MY-COMPUTER.md.")
            else:
                st.info("No profile fields available.")

            # Conversation history: earliest_conversation_preview at top, then snapshots; each row has date; sort earliest first
            st.subheader("💬 Conversation history")
            conversations = []
            conv_source = "none"
            # 1) Earliest conversation preview from full profile (Trino) or full_profiles.json (MCP)
            preview = None
            preview_ts = None
            if profile_row:
                preview = (profile_row.get("earliest_conversation_preview") or "").strip()
                preview_ts = profile_row.get("earliest_conversation_first_at")
                if preview:
                    conv_source = "trino"
            elif app_id and app_id in full_profiles:
                preview = (full_profiles[app_id].get("earliest_conversation_preview") or "").strip()
                preview_ts = full_profiles[app_id].get("earliest_conversation_first_at")
                if preview:
                    conv_source = "full_profiles"
            if preview:
                if preview_ts is not None:
                    try:
                        created_at = preview_ts.strftime("%Y-%m-%d %H:%M") if hasattr(preview_ts, "strftime") else str(preview_ts)[:19].replace("T", " ")
                    except Exception:
                        created_at = str(preview_ts)[:19] if preview_ts else "—"
                else:
                    created_at = "—"
                conversations.append({"created_at": created_at, "content": preview})
            # 2) All conversation messages from Trino (base44_conversation_messages_mongo) when live
            conv_messages = []
            if app_id and trino_configured() and st.session_state.get("trino_live"):
                conv_messages = get_conversation_messages(app_id)
                if conv_messages:
                    conv_source = "trino"
            # 2c) Snapshots from Trino (summaries over time)
            snapshots = []
            if app_id and trino_configured() and st.session_state.get("trino_live"):
                snapshots = get_conversation_snapshots(app_id)
                if snapshots and not conv_messages:
                    conv_source = "trino"
            # 2b) From optional full_profiles JSON (e.g. export from Quix/Trino when live Trino unavailable)
            if not conversations and not snapshots and app_id and app_id in full_profiles:
                extra = full_profiles[app_id]
                snapshots_from_file = extra.get("conversation_snapshots")
                if isinstance(snapshots_from_file, list) and snapshots_from_file:
                    snapshots = [{"created_at": (x.get("created_at") or "—"), "content": (x.get("content") or "").strip()} for x in snapshots_from_file if (x.get("content") or "").strip()]
                    conv_source = "full_profiles"
                else:
                    summary = (extra.get("conversation_summary") or extra.get("app_context_conversation_summary") or "").strip()
                    if summary:
                        conversations = [{"created_at": "—", "content": summary}]
                        conv_source = "full_profiles"
            # 3) Fallback: single summary from app record when no Trino / full_profiles data
            if not conversations and not snapshots and app_record:
                summary = (app_record.get("conversation_summary") or app_record.get("app_context_conversation_summary") or "").strip()
                if summary:
                    conversations = [{"created_at": "—", "content": summary}]
                    conv_source = "app_record"
            # 4) For resolved apps (e.g. WP-linked): never show empty — app exists and was created somehow
            if not conversations and app_record:
                conv_source = "placeholder"
                created_at = "—"
                ts = app_record.get("first_activity_at") or app_record.get("user_apps_last_activity_at")
                if ts is not None:
                    try:
                        created_at = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)[:19].replace("T", " ")
                    except Exception:
                        created_at = str(ts)[:19] if ts else "—"
                conversations = [{"created_at": created_at, "content": "App exists and is linked. No conversation export in current data source; connect Trino or use a JSON export with conversation_summary for full history."}]
            # When we have message-level data, show it; else show preview + snapshots
            if conv_messages:
                import pandas as pd
                df_msg = pd.DataFrame([{"Date & time": m["created_at"], "Role": m.get("role") or "—", "Content": (m.get("content") or "")[:2000] + ("…" if len((m.get("content") or "")) > 2000 else "")} for m in conv_messages])
                st.dataframe(
                    df_msg,
                    column_config={
                        "Date & time": st.column_config.TextColumn("Date & time", width="medium"),
                        "Role": st.column_config.TextColumn("Role", width="small"),
                        "Content": st.column_config.TextColumn("Content", width="large"),
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=min(400, 120 + 60 * len(conv_messages)),
                )
                if snapshots:
                    with st.expander("Conversation summaries (snapshots)", expanded=False):
                        for s in snapshots:
                            st.caption(s.get("created_at", "—"))
                            st.text(s.get("content", "")[:1500] + ("…" if len(s.get("content", "")) > 1500 else ""))
            else:
                conversations = conversations + snapshots
                if conversations:
                    import pandas as pd
                    df = pd.DataFrame(conversations)
                    df = df.rename(columns={"created_at": "Date & time", "content": "Conversation (full)"})
                    st.dataframe(
                        df,
                        column_config={
                            "Date & time": st.column_config.TextColumn("Date & time", width="medium"),
                            "Conversation (full)": st.column_config.TextColumn("Conversation (full)", width="large"),
                        },
                        hide_index=True,
                        use_container_width=True,
                        height=min(400, 120 + 80 * len(conversations)),
                    )
                else:
                    st.caption("No conversation snapshots for this app.")

            st.subheader("📌 UW check vs policy")
            uw = get_uw_for_app(app_id) if app_id else None
            if uw:
                verdict = (uw.get("verdict") or "—").strip()
                reasoning = (uw.get("reasoning") or "").strip()
                insufficient = "insufficient evidence" in reasoning.lower() or "offerings unknown" in (uw.get("non_compliant_subcategories") or "").lower()
                if verdict.lower() == "allowed":
                    st.success(f"**Verdict: {verdict}**")
                elif verdict.lower() in ("restricted", "not-allowed", "not allowed"):
                    st.warning(f"**Verdict: {verdict}**")
                elif "manual review" in verdict.lower():
                    st.info(f"**Verdict: {verdict}** — Review the Evidence section and App Summary in the conclusion file.")
                    st.caption("To regenerate with full conversation and scraped content in the file, click **Run UW for this app** below.")
                else:
                    st.info(f"**Verdict: {verdict}**")
                if insufficient and not (trino_ok or has_meta or has_conv_data):
                    st.caption("This verdict appears because no conversation or app metadata was loaded. Populate full_profiles.json via Trino MCP, then click **Run UW for this app** again.")
                if uw.get("reasoning"):
                    st.markdown("**Why:** " + (uw.get("reasoning") or ""))
                if uw.get("non_compliant_subcategories"):
                    st.markdown("**Non-compliant subcategories:** " + (uw.get("non_compliant_subcategories") or ""))
                if uw.get("scraped_content"):
                    with st.expander("📄 Scraped content", expanded=True):
                        scraped = uw.get("scraped_content") or ""
                        st.text(scraped[:8000] + ("…" if len(scraped) > 8000 else ""))
                for key, title in [("app_summary", "📄 App summary"), ("step1_what_sold", "🛒 What is sold (Step 1)"), ("step2_comparison", "📜 Policy comparison (Step 2)")]:
                    if uw.get(key):
                        with st.expander(title):
                            st.markdown(uw.get(key))
            else:
                st.info("No underwriting result cached for this app. Run the pipeline to generate a verdict.")
                # One-click: run underwriting for this app and refresh
                policy_path = ROOT / "policy" / "policy-excerpt.txt"
                if policy_path.exists():
                    if st.button("▶ Run UW for this app", key="run_uw_one", help="Run underwriting pipeline for this app (includes scraping); then refresh."):
                        temp_dir = ROOT / "data" / "temp_uw"
                        temp_dir.mkdir(parents=True, exist_ok=True)
                        one_app_path = temp_dir / "one_app.json"
                        # Best conversation for pipeline: Trino, or snapshots/messages, or earliest_preview, or app record
                        best_summary = (profile_row.get("app_context_conversation_summary") or "") if profile_row else ""
                        if not best_summary and conv_messages:
                            best_summary = "\n\n".join((m.get("role") or "") + ": " + (m.get("content") or "") for m in conv_messages)
                        if not best_summary and snapshots:
                            best_summary = "\n\n---\n\n".join((s.get("created_at") or "") + "\n" + (s.get("content") or "") for s in snapshots)
                        if not best_summary and app_id and app_id in full_profiles:
                            ep = (full_profiles[app_id].get("earliest_conversation_preview") or "").strip()
                            if ep:
                                best_summary = "[Earliest conversation]\n" + ep
                        if not best_summary:
                            best_summary = (app_record.get("conversation_summary") or app_record.get("app_context_conversation_summary") or "").strip()
                        ud = (profile_row or record_for_profile or {}).get("user_description") or (app_record.get("user_description") or "")
                        cat = (profile_row or record_for_profile or {}).get("categories") or (app_record.get("categories") or "")
                        if ud or cat:
                            best_summary = (best_summary or "") + "\n\n[User description] " + (ud or "—") + "\n[App categories] " + (cat or "—")
                        ep = (
                            (profile_row or {}).get("earliest_conversation_preview")
                            or full_profiles.get(app_id, {}).get("earliest_conversation_preview")
                            or ""
                        )
                        ep = (ep or "").strip()
                        one_app = [
                            {
                                "app_id": app_id,
                                "app_name": app_record.get("app_name") or "—",
                                "app_url": app_record.get("app_url") or "",
                                "conversation_summary": best_summary or "",
                                "user_description": ud or "",
                                "earliest_conversation_preview": ep,
                            }
                        ]
                        one_app_path.write_text(json.dumps(one_app, indent=2), encoding="utf-8")
                        try:
                            out_dir = ROOT / "output"
                            cmd = [
                                sys.executable,
                                str(ROOT / "run_underwriting.py"),
                                "--apps", str(one_app_path),
                                "--policy", str(policy_path),
                                "--out", str(out_dir),
                                "--run-id", "uw_lookup",
                                "--llm", "none",
                            ]
                            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=120)
                            if r.returncode == 0:
                                st.success("Pipeline finished. Refreshing…")
                                st.rerun()
                            else:
                                st.error("Pipeline failed: " + (r.stderr or r.stdout or str(r.returncode)))
                        except subprocess.TimeoutExpired:
                            st.error("Pipeline timed out.")
                        except Exception as e:
                            st.error("Run failed: " + str(e))
                else:
                    st.caption("To run UW from the UI, add policy/policy-excerpt.txt and click the button above.")

            # Debug: why sections might be empty (Trino vs local)
            with st.expander("Debug: data source", expanded=False):
                st.caption("Trino configured: " + ("Yes" if trino_configured() else "No"))
                trino_live = st.session_state.get("trino_live")
                st.caption("Trino live: " + ("Yes" if trino_live else "No"))
                if trino_configured() and not trino_live:
                    if st.button("Retry Trino connection", key="retry_trino"):
                        with st.spinner("Testing Trino…"):
                            st.session_state["trino_live"] = test_connection()
                        st.rerun()
                st.caption("Full profile loaded: " + ("Yes" if profile_row else "No (using app record" + (" + full_profiles.json)" if (app_id and app_id in full_profiles) else " only)")))
                st.caption("Conversation rows: " + str(len(conversations)))
                if full_profiles:
                    st.caption("Full profiles file: loaded (" + str(len(full_profiles)) + " apps). Set FULL_PROFILES_JSON_PATH or add data/full_profiles.json for extended profile + conversation when Trino is off.")
                if app_record:
                    has_summary = bool((app_record.get("conversation_summary") or app_record.get("app_context_conversation_summary") or "").strip())
                    st.caption("Conversation source (when Trino off): fallback from app record; has summary: " + ("Yes" if has_summary else "No"))
                if trino_configured() and not profile_row:
                    err = get_last_trino_error()
                    if err:
                        st.caption("Last Trino error: " + err)
                st.caption("For full profile when Trino is unavailable, use a JSON export that includes first_activity_at, user_description, etc., and set APPS_JSON_PATH.")

# Add button runs here every time so the click is handled (Streamlit re-runs on button click)
pending = st.session_state.get("pending_add_app_id")
if pending:
    st.markdown("---")
    st.warning("App not in your list. Add it below, then click **Look up** again. For full data for many apps, use a JSON export and set `APPS_JSON_PATH` in `.env`.")
    add_name = st.text_input("App name (optional)", key="add_name", placeholder="e.g. My App Name")
    add_url = st.text_input(
        "App URL (paste or pick up)",
        key="add_url",
        placeholder="e.g. https://my-app.base44.app",
        help="Paste the full app URL here for scraping and full profile.",
    )
    if st.button("➕ Add this App ID to my list", type="primary", key="add_to_list"):
        ok, err = add_app_to_user_list(
            pending,
            app_name=(add_name or None),
            app_url=(add_url or None),
        )
        if ok:
            del st.session_state["pending_add_app_id"]
            st.success("✅ **Added.** Now click **Look up** again (the red button at the top).")
        else:
            st.error("Could not add: " + (err or "check file permissions."))
