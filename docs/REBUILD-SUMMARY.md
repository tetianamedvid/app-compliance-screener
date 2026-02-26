# Dashboard Rebuild Summary (MCP Trino Only)

## What Was Done

### 1. Data from Trino MCP
- **Apps with metadata:** 132 WP apps with app_id, app_name, app_url, msid, account_id, user_description, public_settings, categories
- **User logs:** 80 apps with user_app_events_count, first_activity_at, last_activity_at
- **Saved to:** `data/real_apps.json`, `data/trino_app_metadata.json`, `data/trino_user_logs.json`, `data/full_profiles.json`

### 2. Dashboard Changes
- **TRINO_USE_LOCAL_ONLY=1** in `.env` — uses only local data (no direct Trino)
- **Removed blue info box** when basic profile exists (app_id, app_name, app_url)
- **Added full_profiles.json upload** in "Update app list from export" expander
- **Earliest conversation preview** from full_profiles used in Run UW when no snapshots
- **Conversation section** shows earliest_conversation_preview, conversation_snapshots, or placeholder

### 3. Pipeline (Run UW for this app)
- **Scraping:** On by default (no --no-scrape)
- **LLM:** none (no API keys) → verdict "Manual Review Required"
- **Conclusion:** Evidence section with full conversation + scraped content
- **Output:** `output/run_uw_lookup/conclusion_<app_id>.md`

### 4. App Profile Fields
From `uw_app/profile.py` and real_apps/full_profiles:
- app_id, app_name, app_url, msid, WP account id
- **User Description** (from base44_user_generated_apps_v2_mongo)
- **Public Settings** (from base44_user_generated_apps_v2_mongo)
- **Categories** (e.g. ["Finance", "Education", "Data & Analytics"])
- first_activity_at, user_apps_last_activity_at, user_app_events_count

### 5. Conversation History
- **earliest_conversation_preview** from prod.base44.base44_conversation_messages_mongo (first 10 messages of earliest conversation)
- **conversation_snapshots** from prod.marketing.base44_app_context_snapshots_mongo
- **conversation_summary** from app record (fallback)

## How to Run

1. **Start dashboard:**
   ```bash
   cd "/Users/tetianamedvid/Documents/Base44 analysis"
   streamlit run streamlit_uw.py
   ```

2. **Open:** http://localhost:8501

3. **Look up an app:** Paste App ID (e.g. 6999400b6985907082a94b33 for LifeGuard Quiz), click Look up

4. **Run UW:** Click "Run UW for this app" to generate conclusion (scrape + Manual Review verdict)

5. **Upload data:** Use "Update app list from export" to upload CSV/JSON or full_profiles.json

## Data Refresh (MCP Only)

When you run Trino MCP queries in Cursor:
1. Save results to `data/trino_user_logs.json`, `data/trino_conversations.json`, `data/trino_app_metadata.json`, `data/trino_earliest_conversation_preview.json` (format: `{rows, col_names}`)
2. Run: `python3 scripts/build_full_profiles_from_trino.py`
3. For app list: parse MCP apps response → `data/real_apps.json`
