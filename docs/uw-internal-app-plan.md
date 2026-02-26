# UW Internal App — Plan

Internal app for the Underwriting team: **restricted access**, **live and functional**. User enters **app_id**, **msid**, or **WP account id** and gets quickly:

1. **App profile** — information from the full-app-profile query as a **transposed table** (one row per attribute).
2. **UW check vs policy** — what we do today: app summary (middleman) + policy comparison (verdict, reasoning, non-compliant subcategories).

---

## 1. Accuracy first

Before going live, ensure pipeline accuracy:

- **Policy prompt** (`prompts/policy-comparison.md`): Step 1 must restate only what the app summary says the app sells; it must not list policy categories (e.g. firearms, weapons, ammunition) unless the summary explicitly states the app sells those. Step 2 must only compare items that appear in Step 1, with an explicit example in the prompt.
- **Step 1 sanity check** (`run_underwriting.py`): If the policy step output lists prohibited items in Step 1 (e.g. "functional firearms", "weapon components") that do not appear in the app summary, the pipeline discards the LLM output and substitutes the "Restricted / insufficient evidence" template.
- **Step 2 consistency check** (`run_underwriting.py`): If Step 2 mentions policy categories (e.g. firearms, ammunition, Subcategory 6) that Step 1 does not contain, the output is treated as invalid and the same safe template is used.
Re-running the pipeline overwrites existing conclusion files in `output/run_<run_id>/`. To refresh conclusions, run `run_underwriting.py` again with the desired `--apps` and `--run-id`; the UW app shows the latest cached conclusion (by file mtime). **Recommended:** Re-run on the 7 production test apps and spot-check; optionally add a small golden set and regression script.

---

## 2. How to get the data

### 2.1 Resolve identifier → app_id (and minimal app record for UW)

User input is one of:

| Input type   | Example | How to resolve |
|-------------|---------|----------------|
| **app_id**  | `698406273ade17b9bd851188` | Use as-is. Fetch app record (app_name, app_url, conversation_summary) for UW. |
| **msid**    | `c6ab1a9b-1830-4f53-a221-5d2ae0597796` | Trino: `SELECT app_id, app_name, app_url FROM prod_encrypted.payments.pp_base44_apps_replica WHERE msid = ?`. Then get conversation_summary from base44_app_context_snapshots_mongo or same query if available. |
| **WP account id** | WixPayments `account_id` | Trino: join `pp_base44_apps_replica ba` with `prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid` where `ap.account_id = ?`; return `ba.app_id`, `ba.app_name`, `ba.app_url`. Conversation summary from `prod.marketing.base44_app_context_snapshots_mongo m ON m.app_id = ba.app_id`. |

A single “resolve” query that supports all three is in `docs/trino-query-resolve-app.sql`. It returns one row: `app_id`, `app_name`, `app_url`, `conversation_summary`, `msid`, `wp_account_id` (for display in the app profile).

### 2.2 App profile (transposed table)

- **Source:** `docs/trino-query-full-app-profile.sql`.
- **How:** Run the query with `filter_app_ids` restricted to the resolved `app_id` (e.g. `AND ba.app_id IN (SELECT app_id FROM (VALUES ('<app_id>')) AS t(app_id))` or parameterized).
- **Output for UI:** One row per column → show as **transposed table**: column name | value (e.g. `app_id` | `69840...`, `agent_conversations_count` | `12`, …). Omit null/empty or truncate long text so the table stays readable.

### 2.3 UW check vs policy

- **Option A — Cached:** Store conclusions per run in `output/run_<run_id>/conclusion_<app_id>.md`. The app looks up the latest run (or a designated run) and, if a conclusion exists for this `app_id`, parses it and returns: app summary, Step 1 (what is sold), Step 2 (comparison), verdict, reasoning, non-compliant subcategories. **Fast.**
- **Option B — On-demand:** If no cached conclusion (or user clicks “Re-run UW”), call the same pipeline as `run_underwriting.py` for **one app**: build app record from Trino (app_id, app_name, app_url, conversation_summary), optionally scrape app_url, run middleman → policy step, write conclusion to cache, return result. **Slower but always fresh.**

Recommendation: **default to cached**; add a “Re-run UW” action for on-demand.

---

## 3. Architecture (how to build it)

### 3.1 Stack (suggested)

- **Backend:** FastAPI (Python). Endpoints: resolve (app_id | msid | wp_account_id), get profile (by app_id), get UW result (by app_id, from cache or on-demand).
- **Trino:** Run parameterized queries (resolve, full app profile) via your existing Trino access (MCP or direct connector). If Trino is not callable from the app server, use a small “data service” that runs the queries and the UW app calls it.
- **Frontend:** Simple internal UI: search box (identifier type + value), then two sections: “App profile” (transposed table) and “UW check” (verdict, reasoning, app summary, Step 1/2). Option: Streamlit for a fast MVP; or React/Vue if you need stricter access control and richer UI.
- **Restricted access:** Internal only (VPN / private network). Add auth: API key, SSO, or IdP (e.g. Google Workspace) so only the UW team can open the app. No public internet exposure.

### 3.2 Data flow

1. User enters identifier (e.g. WP account id) and type.
2. Backend calls **resolve** → gets `app_id`, `app_name`, `app_url`, `conversation_summary`.
3. Backend calls **Trino full-app-profile** (filter by `app_id`) → one row; convert to transposed table.
4. Backend looks up **cached conclusion** for `app_id` (e.g. from `output/run_<run_id>/conclusion_<app_id>.md`); if missing, optionally run pipeline for one app and cache.
5. Return JSON: `{ "app_id", "app_profile": [{"field", "value"}, ...], "uw": { "verdict", "reasoning", "app_summary", "step1_what_sold", "step2_comparison", "non_compliant_subcategories" } }`.
6. Frontend renders app profile table + UW section.

### 3.3 Files to add (scaffold in repo)

- `docs/trino-query-resolve-app.sql` — single query to resolve app_id/msid/wp_account_id to one row (app_id, app_name, app_url, conversation_summary, msid, wp_account_id).
- `uw_app/` — FastAPI app: `main.py` (resolve, profile, uw endpoints); `trino_client.py` (stub or real Trino); `uw_cache.py` (read/write conclusion by app_id); optional `streamlit_ui.py` or separate frontend.
- `docs/uw-internal-app-plan.md` — this plan.

---

## 4. Implementation order

1. **Accuracy:** Finalize tuning; re-run 7-app set; confirm no hallucinations.
2. **Resolve query:** Add and test `trino-query-resolve-app.sql` (by app_id, msid, wp_account_id).
3. **Backend API:** Implement resolve (stub Trino if needed), profile (stub or Trino), UW (read from existing conclusion files).
4. **Transposed profile:** From full-app-profile row → list of `{ "field": col, "value": val }`; exclude or truncate long blobs.
5. **Frontend:** Single search + results (profile table + UW block).
6. **Auth:** Restrict by network and/or API key / SSO.
7. **On-demand UW:** Optional “Re-run UW” that calls `run_underwriting` for one app and updates cache.

Once accuracy is ensured and this app is live, the UW team can look up any app by app_id, msid, or WP account id and get the app profile plus UW check in one place.
