# Old UW Pipeline Scripts

These scripts belong to the **LLM-based underwriting pipeline** (the original system).
They are used by `streamlit_uw.py` and `run_underwriting.py`.

They were moved here to keep the main `scripts/` folder clean — only the newer
keyword-based screener scripts remain there.

## What each script does

| Script | Purpose |
|--------|---------|
| `run_all.py` | Orchestrator: validate, fetch profiles, run UW for missing apps |
| `run_uw_for_missing.py` | Run underwriting for apps that don't have a conclusion yet |
| `fetch_full_profiles_from_trino.py` | Fetch user logs, conversations, metadata from Trino |
| `build_full_profiles_from_trino.py` | Merge Trino JSON exports into `full_profiles.json` |
| `save_mcp_trino_results.py` | Parse MCP response files into Trino JSON files |
| `import_apps_export.py` | Import CSV/JSON app export into `real_apps.json` |
| `merge_export_chunks.py` | Merge chunked exports into one `real_apps.json` |
| `merge_trino_wp_apps_into_real.py` | Merge `trino_wp_apps.json` into `real_apps.json` |
| `write_trino_mcp_result_to_app_json.py` | Convert MCP output to `real_apps.json` |
| `conclusions_to_csv.py` | Export UW conclusion `.md` files to CSV |
| `qa_dashboard_ready.py` | QA check: app list + profiles exist |
| `qa_uw_lookup.py` | QA check: resolve, profile, Trino connection |
| `debug_trino_conversation.py` | Debug: print conversation history for an app |
