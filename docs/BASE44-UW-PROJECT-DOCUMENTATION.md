# Base44 Underwriting Lookup — Full Project Documentation

**Version:** 3.0  
**Date:** March 6, 2026  
**Project:** Base44 Underwriting Lookup (UW Lookup) + App Compliance Screener

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Scraping Engine](#3-scraping-engine)
4. [Underwriting Pipeline](#4-underwriting-pipeline)
5. [Dashboard (Streamlit)](#5-dashboard-streamlit)
6. [App Compliance Screener](#6-app-compliance-screener)
7. [Data Sources](#7-data-sources)
8. [File Layout](#8-file-layout)
9. [Setup & Installation](#9-setup--installation)
10. [Running the App](#10-running-the-app)
11. [LLM Options](#11-llm-options)
12. [Trino Configuration](#12-trino-configuration)
13. [Trino Data Auto-Refresh](#13-trino-data-auto-refresh)
14. [Base44 Platform Knowledge](#14-base44-platform-knowledge)
15. [Security & Compliance](#15-security--compliance)
16. [Audit Checklist](#16-audit-checklist)

---

## 1. Project Overview

### Purpose

Two complementary tools for policy compliance review of WixPayments-connected Base44 apps:

**Tool 1 — UW Lookup dashboard** (`streamlit_uw.py`, port 8501):
- Look up any app by **App ID**, **MSID**, or **WixPayments account ID**
- View full app profile, builder conversation history, and cached underwriting verdict
- Run the two-step LLM underwriting pipeline for a single app or batch
- Scrape and analyze any app URL (Standalone URL check)

**Tool 2 — App Compliance Screener** (`streamlit_screener.py`, port 8502):
- Paste one or many app URLs → instant scrape + rule-based policy classification → verdict
- Uses Trino conversation summary as additional signal (catches auth-walled apps like cannabis shops with fake descriptions)
- Persistent findings table with analyst review workflow (Pending / Reviewed / Escalated / Approved / Declined)
- Batch-screen the full Trino population (240+ apps) via `scripts/screen_with_trino.py`

### Business Context

- Supports policy compliance review for apps connected to WixPayments
- Evidence: builder conversation summary (from Trino), scraped public app content, Base44 API data, and policy taxonomy
- Screener catch rate: ~48% on known non-compliant apps (URL-only); higher when Trino conversation summary is included
- Remaining misses: auth-walled apps with no public content, business-context violations (scams, IPR) requiring human judgment
- All verdicts are advisory — human review required before action

---

## 2. Architecture

### High-Level Flow

```
[App list: real_apps.json + user_apps.json]
         │
         ▼
[Resolve: App ID / MSID / WP account ID → single app record]
         │
         ▼
[Profile: Trino full profile OR full_profiles.json + app record]
         │
         ▼
[Conversation: Trino messages/snapshots OR full_profiles]
         │
         ▼
[UW verdict: from cached conclusion_<app_id>.md, or Run UW pipeline]
         │
         ▼
[Pipeline: scrape → middleman summary → policy comparison → verdict]
```

### Standalone URL Check Flow

```
[User enters URL]
       │
       ▼
[Scrape: Base44 API + meta tags + frontend config + JS signals (parallel)]
       │
       ▼
[Middleman: LLM or rule-based template → app summary]
       │
       ▼
[Policy comparison: LLM or rule-based template → verdict]
       │
       ▼
[Display: verdict, reasoning, scraped content, app summary]
```

### Components

| Component | File | Purpose |
|-----------|------|---------|
| UW Lookup dashboard | `streamlit_uw.py` | Streamlit UI (http://localhost:8501) |
| Compliance Screener | `streamlit_screener.py` | Batch URL screener (http://localhost:8502) |
| Entry point | `run_uw_app.py` | Sync + start UW Lookup dashboard |
| UW Pipeline | `run_underwriting.py` | Two-step pipeline (middleman + policy) |
| App Screener | `uw_app/app_screener.py` | URL → scrape + classify → ScreenResult |
| Policy Classifier | `uw_app/policy_classifier.py` | Rule-based Stripe policy taxonomy (keyword matching) |
| Findings Store | `uw_app/findings_store.py` | Persistent JSONL log of all screened apps |
| Review Store | `uw_app/review_store.py` | Analyst review status per app_id |
| Trino client | `uw_app/trino_client.py` | Trino queries and connection |
| Profile | `uw_app/profile.py` | Profile table from record or Trino row |
| Resolve | `uw_app/resolve.py` | App list merge and resolve |
| UW cache | `uw_app/uw_cache.py` | Read cached conclusions for display |
| Data refresh | `uw_app/data_refresh.py` | Auto-refresh from export file |

---

## 3. Scraping Engine

### Architecture (Base44-Aware)

The scraper uses a layered strategy optimized for Base44's React SPA architecture:

#### For Base44 URLs (`base44.app` / `base44.com`)

**Fast mode (default, ~1-3s):** All fetches run in parallel — no browser needed.

| Layer | What it fetches | Time |
|-------|----------------|------|
| **Base44 Public API** | App ID (from URL path), app name, description, auth config, visibility, login methods, payment signals, creation date, slug | ~1s |
| **SEO Meta Tags** | `<title>`, `og:description`, `og:image` from the static HTML shell | ~1s |
| **Frontend Config** (`/api/frontend-config.js`) | SDK backend URL, apps domain, Cloudflare Turnstile status, Google auth, Fiverr integration | ~1s |
| **Sitemap** (`/sitemap.xml`) | Page URLs for app structure | ~1s |

**Deep mode (opt-in, ~5-15s):** Adds Playwright browser rendering on top of the fast path.

| Layer | What it fetches | Time |
|-------|----------------|------|
| All fast-mode layers | (same as above) | ~1-3s |
| **JS Bundle Scan** | App-specific JS chunks scanned for entity names, backend function names, payment-related functions, API endpoint patterns | ~2-3s |
| **Playwright Rendering** | Full JS-rendered page content, API response interception, entity data capture, auth wall detection, product extraction, deep link following | ~5-10s |

#### For Non-Base44 URLs

1. **urllib fetch** (~1s) — fast HTML download + text extraction
2. **Sitemap** (~1s, parallel with urllib)
3. **Base44 detection** — if HTML contains Base44 markers, probes Base44 APIs
4. **Playwright** — only if urllib fails (JS-rendered content needed)

### Key Scraping Features

- **Persistent browser singleton** — Playwright browser launches once and stays alive across all calls, eliminating ~8s startup cost
- **Browser pre-warming** — Streamlit startup launches browser in background thread
- **App ID from URL path** — extracts directly from `/app/{id}` pattern, skips roundtrip API call
- **Smart DOM waiting** — polls React root for children (200ms intervals, max 2s) instead of fixed delays
- **Auth wall classification** — distinguishes private apps (by design), public apps with auth, and broken apps
- **Entity data capture** — intercepts `/api/entities/` responses during Playwright rendering
- **Backend function detection** — watches for `/functions/` calls (payment, email, checkout)
- **Ad/analytics blocking** — aborts requests to Google Analytics, Facebook, Hotjar, etc. during Playwright
- **Auto URL normalization** — auto-prepends `https://` to bare domains

### What Gets Extracted

| Signal | Source | Example |
|--------|--------|---------|
| App name | Base44 API, meta tags | "Commission Tracker Pro" |
| Description | Base44 API (`user_description`), `og:description` | "Track your daily, weekly..." |
| App ID | URL path, Base44 API | `699a64f6d109270a800724d1` |
| Auth config | Base44 API (`auth_config`) | Google + Email/password enabled |
| Visibility | Base44 API | public / private |
| Payment signals | Base44 API, Playwright intercept | Stripe enabled, payment functions |
| Entity types | JS bundle scan, Playwright intercept | products, orders, users |
| Backend functions | JS bundle scan, Playwright intercept | createCheckout, processPayment |
| Integrations | Frontend config | Fiverr (env=prod) |
| Anti-bot | Frontend config | Cloudflare Turnstile enabled |
| Creation date | Base44 API | 2026-02-22 |
| Products/items | JSON-LD, DOM extraction | Name, price, description |
| Sitemap pages | sitemap.xml | List of app page URLs |

---

## 4. Underwriting Pipeline

### Two-Step Process

**Step 1 — Middleman (App Summary):**
- Input: app name, URL, conversation summary, scraped content, user description
- Output: structured summary with intent, creation context, what is sold, what buyer gets
- Prompt: `prompts/app-summary-middleman.md`

**Step 2 — Policy Comparison:**
- Input: app summary + policy excerpt
- Output: what is sold (from summary), policy match, verdict + reasoning + non-compliant subcategories
- Prompt: `prompts/policy-comparison.md`

### Rule-Based Template (No LLM)

When no LLM is available (`--llm none` or "Fast (no LLM)" in dashboard), the template:

1. **Parses structured fields** from scraped content — app name, description, auth config, entity types, payment signals, creation date, slug
2. **Infers intent** from description keywords (tool/utility, e-commerce, booking, etc.)
3. **Detects auth methods** from `auth_config` JSON (Google, Email/password, Microsoft, Facebook, Apple)
4. **Classifies app type** — tracking/management tool, shop/store, booking app
5. **Extracts buyer value** from keyword patterns (subscriptions, tickets, courses, tracking tools)
6. **Lists app characteristics** — login methods, entity types, backend functions, integrations

### Caching

- App summaries cached in `output/uw_cache/` by app_id
- On re-runs, cached summaries reuse (only policy step runs)
- Use `--no-cache` to force regeneration

### Batch Processing

- `--workers 8` — parallel processing (scraping + LLM calls concurrent)
- `--delay 0` — skip 0.5s pause between LLM calls
- `--no-scrape` — skip URL scraping when conversation data is sufficient

---

## 5. Dashboard (Streamlit)

### URL: http://localhost:8501

### Sections

1. **Standalone APP URL check** (top)
   - Paste any URL (auto-prepends `https://` if missing)
   - Default: Fast mode (no LLM, no browser) — ~1-3 seconds
   - Optional: Deep scrape (Playwright rendering) and LLM analysis
   - Shows: verdict, app summary, scraped content, Q&A interface

2. **App Lookup** (main)
   - Look up by App ID (24-char), MSID (UUID), or WixPayments account ID (UUID)
   - Shows: app profile table, conversation history, UW verdict
   - Actions: Run UW for this app, Ask questions about the app

3. **App List** (expandable)
   - Shows all apps in merged list
   - Reload, update from export file

### Analysis Modes

| Mode | Speed | Quality | Requirements |
|------|-------|---------|-------------|
| Fast (no LLM) | ~1-3s | Rule-based extraction | None |
| Auto (LLM) | ~30-120s | Full LLM analysis | Ollama or OpenAI |
| OpenAI | ~5-15s | Best quality | `OPENAI_API_KEY` in `.env` |
| Ollama | ~30-120s | Good quality | Ollama running locally |

---

## 6. App Compliance Screener

### Purpose

Fast, rule-based batch screening of app URLs against the Stripe Supportability policy taxonomy. Designed to process the full population of WixPayments-connected apps (240+) in minutes, without requiring an LLM.

### How It Works

1. **Input:** one or many app URLs (paste in UI, or load from Trino population JSON)
2. **Scrape:** Base44 public APIs + meta tags + frontend config → structured identity signals (fast mode, ~1–3s). Optional deep Playwright rendering.
3. **Trino context:** if `conversation_summary` and `trino_description` are provided, they are passed to the classifier alongside scraped content. This is critical for auth-walled apps where the public page reveals nothing — the builder conversation often contains the real product.
4. **Classify:** rule-based keyword matching against `uw_app/policy_classifier.py` — covers 30+ policy subcategories (gambling, cannabis, firearms, crypto, healthcare/prescriptions, debt collection, visa scams, etc.)
5. **Verdict:** `Not Supportable` (red) / `Not Enabled for Wix` (orange) / `Restricted — Review` (orange) / `Likely Supportable` (green) / `Insufficient Data` (gray)
6. **Persist:** every result saved to `data/findings.jsonl` with analyst review status

### Key Design Decisions

| Decision | Rationale |
|---|---|
| Rule-based (no LLM) | ~1–3s per app; can process 240 apps in under 2 minutes |
| "Insufficient Data" instead of "Likely Supportable" | If no content scraped and no Trino context, don't falsely clear the app |
| Conversation summary as classifier input | Catches ZazaStyle-type cases: app description says "fashion", conversation says "PACKMAN cannabis shop" |
| Cannabis slang keywords | Added: `zaza`, `packman`, `runtz strain`, `thca flower`, `delta-8`, `live resin`, `smoke shop`, etc. |
| Telehealth as separate subcategory | `symptom navigator`, `telemedicine platform`, `patient portal`, `emr system` |

### Accuracy (Validated March 2026)

Tested against 27 known analyst-reviewed non-compliant apps:
- **48% correctly flagged** from URL scraping alone
- **Higher** when Trino conversation summary is included (auth-walled apps)
- Remaining misses: private apps (no public content), business-context violations (scams, IPR) — require human judgment

### Running the Screener

```bash
# Start the Screener UI
streamlit run streamlit_screener.py --server.port 8502

# Validate accuracy against known non-compliant apps
python3 scripts/validate_screener.py

# Batch-screen the 155 non-flagged apps (URL only)
python3 scripts/screen_non_flagged.py

# Batch-screen the full Trino population with conversation summaries (recommended)
python3 scripts/screen_with_trino.py --context-only   # instant, no scraping
python3 scripts/screen_with_trino.py                  # full scrape + Trino context
```

### Passing Trino Context Programmatically

```python
from uw_app.app_screener import screen, screen_batch

# Single app with Trino data
result = screen(
    app_url,
    conversation_summary=row["conversation_summary"],
    trino_description=row["trino_description"],
    app_name_hint=row["trino_app_name"],
)

# Batch with Trino data
results = screen_batch(urls, trino_rows=[
    {"url": r["app_url"], "conversation_summary": r["conversation_summary"],
     "trino_description": r["trino_description"], "app_name_hint": r["trino_app_name"]}
    for r in trino_rows
])
```

---

## 7. Data Sources

### Trino (When Configured)

| Table | Fields |
|-------|--------|
| `prod_encrypted.payments.pp_base44_apps_replica` | app_id, app_name, app_url, msid |
| `prod.payments.wp_accounts_replica` | msid → account_id (WixPayments) |
| `prod.marketing.base44_user_generated_apps_v2_mongo` | user_description, public_settings, categories |
| `prod.marketing.base44_app_context_snapshots_mongo` | conversation snapshots per app |
| `prod.base44.base44_conversation_messages_mongo` | message-level conversation per app |

### Local JSON

| File | Purpose |
|------|---------|
| `data/real_apps.json` (or `APPS_JSON_PATH`) | Main app list |
| `data/user_apps.json` | User-added/override apps |
| `data/full_profiles.json` (or `FULL_PROFILES_JSON_PATH`) | Extended profile + conversation (from Trino fetch) |
| `data/trino_full_population.json` | Full UW population (240 apps) with conversation summaries — auto-refreshed every 24h |
| `data/findings.jsonl` | Persistent log of every screened app (JSONL, one result per line) |

### Base44 Public APIs (No Auth Required)

| Endpoint | Returns |
|----------|---------|
| URL path `/app/{id}` | App ID directly from URL |
| `/api/apps/public/login-info/by-id/{app_id}` | App name, description, auth config, visibility, payments, integrations |
| `/api/apps/public/prod/domain/{domain}` | App ID for custom domains |
| `/api/frontend-config.js` | Platform config (SDK URL, Fiverr, Turnstile, Google auth) |
| `/api/billing/tiers` | Platform pricing tiers |

### Output

| Path | Content |
|------|---------|
| `output/run_<run_id>/conclusion_<app_id>.md` | Per-app UW conclusion (verdict, reasoning, evidence) |
| `output/run_<run_id>/manifest.json` | Run metadata (started_at, app_ids) |
| `output/uw_cache/<app_id>.json` | Cached app summary (middleman step) |
| `output/trino_screen_results.csv` | Full screener results for Trino population (with Trino context) |
| `output/trino_context_only_flags.csv` | Context-only classification of Trino population (no scraping) |
| `output/non_flagged_screen_results.csv` | Screener results for the non-flagged CSV batch |

---

## 7. File Layout

```
Base44 scrapping/
├── run_uw_app.py                    # Entry: sync + start UW Lookup dashboard
├── run_underwriting.py              # UW pipeline + scraping engine
├── streamlit_uw.py                  # UW Lookup dashboard (port 8501)
├── streamlit_screener.py            # App Compliance Screener UI (port 8502)
├── requirements.txt                 # Python dependencies
├── .env                             # Config (TRINO_HOST, OPENAI_API_KEY, etc.)
├── .env.example                     # Template for .env
│
├── uw_app/                          # Core application modules
│   ├── __init__.py
│   ├── app_screener.py              # URL → scrape + classify → ScreenResult
│   ├── policy_classifier.py         # Rule-based Stripe policy taxonomy
│   ├── findings_store.py            # Persistent JSONL findings log
│   ├── review_store.py              # Analyst review status per app_id
│   ├── trino_client.py              # Trino queries and connection
│   ├── profile.py                   # Profile table builder
│   ├── resolve.py                   # App list merge and resolve
│   ├── uw_cache.py                  # Read cached conclusions
│   ├── data_refresh.py              # Auto-refresh from export
│   ├── main.py                      # Entry helpers
│   └── streamlit_app.py             # Legacy dashboard module
│
├── pages/                           # Streamlit multi-page app pages
│   └── 1_Findings_Table.py          # Findings table page (screener)
│
├── scripts/                         # Utility scripts
│   ├── validate_screener.py         # Validate accuracy vs known non-compliant apps
│   ├── screen_non_flagged.py        # Batch-screen 155 non-flagged apps (URL only)
│   ├── screen_with_trino.py         # Batch-screen Trino population + conversation context
│   ├── run_all.py                   # Validate + fetch + UW missing
│   ├── run_uw_for_missing.py        # Batch UW for apps without conclusion
│   ├── fetch_full_profiles_from_trino.py  # Trino → JSON
│   ├── build_full_profiles_from_trino.py  # Build full_profiles.json
│   ├── import_apps_export.py        # Import CSV/JSON export
│   ├── test_live_trino.py           # Test Trino connection
│   ├── qa_uw_lookup.py              # QA resolve + profile
│   ├── qa_dashboard_ready.py        # Check dashboard readiness
│   ├── scrape_app_deep.py           # Standalone deep scrape
│   ├── conclusions_to_csv.py        # Export conclusions to CSV
│   └── ...                          # Other utility scripts
│
├── prompts/                         # LLM prompt templates
│   ├── app-summary-middleman.md     # Middleman step prompt
│   ├── policy-comparison.md         # Policy comparison prompt
│   └── underwriting-conclusion.md   # Conclusion format
│
├── policy/
│   └── policy-excerpt.txt           # Policy text for LLM comparison
│
├── data/                            # App data
│   ├── real_apps.json               # Main app list
│   ├── user_apps.json               # User-added apps
│   ├── full_profiles.json           # Extended profiles (from Trino)
│   ├── production_test_apps.json    # 7 QA sample apps
│   ├── trino_full_population.json   # Live Trino population (240 apps) — auto-refreshed daily
│   ├── findings.jsonl               # Persistent screener findings log
│   ├── Non-flagged apps - Sheet1.csv    # 155 analyst-reviewed clean apps
│   └── non-compliant apps - Sheet1.csv  # 36 analyst-reviewed non-compliant apps
│
├── output/                          # Pipeline output
│   ├── run_<run_id>/
│   │   ├── conclusion_<app_id>.md
│   │   └── manifest.json
│   ├── uw_cache/                    # Cached app summaries (middleman step)
│   ├── trino_screen_results.csv     # Full screener results (Trino population)
│   ├── trino_context_only_flags.csv # Context-only flags (no scraping)
│   └── non_flagged_screen_results.csv  # Screener results for non-flagged batch
│
├── .cursor/rules/                   # Cursor AI rules
│   ├── underwriting.mdc             # UW output format and pipeline rules
│   ├── docs-and-responses.mdc       # Step-by-step docs for non-devs
│   ├── trino-data-refresh.mdc       # Auto-refresh Trino population every 24h
│   └── validation-wp-app-list.mdc   # Data validation rules
│
└── docs/                            # Documentation
    ├── BASE44-UW-PROJECT-DOCUMENTATION.md  # This file
    ├── USER-REQUESTS-IN-THIS-PROJECT.txt
    ├── PROJECT-DESCRIPTION-FOR-AUDIT.txt
    ├── RUN-UW-APP-ON-MY-COMPUTER.md
    ├── TRINO-SETUP-AND-TROUBLESHOOTING.md
    ├── DEBUG-TRINO-CONVERSATION.md
    ├── uw-internal-app-plan.md
    └── base44-docs/                 # Base44 platform documentation
```

---

## 8. Setup & Installation

### Prerequisites

- **Python 3.9+**
- **Terminal** (Mac: Terminal.app; Windows: Command Prompt or PowerShell)

### Step 1: Install Dependencies

```bash
cd "/path/to/Base44 analysis"
pip3 install -r requirements.txt
```

### Step 2: Install Playwright Browser (For Deep Scraping)

```bash
python3 -m playwright install chromium
```

Without this, scraped content for JS-rendered apps will be minimal.

### Step 3: Create `.env`

```bash
cp .env.example .env
```

Edit `.env` to set:
- `TRINO_HOST=trino.yourcompany.com` (for live data)
- `OPENAI_API_KEY=sk-...` (optional, for LLM analysis)
- `APPS_JSON_PATH=data/real_apps.json` (path to app list)

---

## 9. Running the App

### Quick Start

```bash
cd "/path/to/Base44 analysis"
python3 run_uw_app.py
```

Open http://localhost:8501 in your browser.

### Skip Sync (Fast Start)

```bash
SKIP_STARTUP_SYNC=1 python3 run_uw_app.py
```

### Run Pipeline on a JSON File

```bash
python3 run_underwriting.py \
  --apps data/real_apps.json \
  --policy policy/policy-excerpt.txt \
  --out output \
  --run-id my_run \
  --llm none
```

### Batch UW (With Parallel Workers)

```bash
python3 run_underwriting.py \
  --apps data/real_apps.json \
  --policy policy/policy-excerpt.txt \
  --out output \
  --run-id fast_run \
  --workers 8 \
  --delay 0
```

### UW for Missing Apps

```bash
python3 scripts/run_uw_for_missing.py
```

---

## 10. LLM Options

No `OPENAI_API_KEY` required. The pipeline works with any of these:

| Option | Setup | Speed | Quality |
|--------|-------|-------|---------|
| **Template only** (`--llm none`) | Nothing needed | ~1-3s | Rule-based extraction |
| **Ollama (local)** | Install [Ollama](https://ollama.com), `ollama pull llama3.1:8b` | ~30-120s/app | Good |
| **OpenAI** | Set `OPENAI_API_KEY` in `.env` | ~5-15s/app | Best |
| **Auto** | Either Ollama or OpenAI | Varies | Tries OpenAI → Ollama → template |

### Ollama Performance Note

Ollama `llama3.1:8b` on a standard MacBook may take 60+ seconds per LLM call. With 2 calls per app (middleman + policy), expect ~2 minutes per app. The dashboard defaults to "Fast (no LLM)" to avoid this latency. Switch to "Auto" or "OpenAI" when you need LLM-quality analysis.

---

## 11. Trino Configuration

### Option A: Live Trino (Look Up Any App)

In `.env`:
```
TRINO_HOST=trino.yourcompany.com
```

Connect to VPN, start the app, SSO login when prompted.

### Option B: Local Data Only

In `.env`:
```
TRINO_USE_LOCAL_ONLY=1
APPS_JSON_PATH=data/real_apps.json
```

### Option C: Browser Session Cookie

If you can run Trino queries in Quix:
1. Log into Quix in your browser
2. Copy session cookie from DevTools → Application → Cookies
3. In `.env`: `TRINO_COOKIE=session=THE_VALUE_YOU_COPIED`

### Troubleshooting

- **"403 Forbidden"** → You're using the Quix web UI address, not the Trino API host
- **Test connection:** `python3 scripts/test_live_trino.py`
- **Full guide:** `docs/TRINO-SETUP-AND-TROUBLESHOOTING.md`

---

## 13. Trino Data Auto-Refresh

The Cursor AI rule `.cursor/rules/trino-data-refresh.mdc` ensures the AI always works with fresh Trino data.

### How it works

At the start of every Cursor conversation, the AI silently checks the age of `data/trino_full_population.json`:

- **File missing or older than 24 hours** → AI runs the Trino population query via MCP in 3 batches of 80 rows, merges results, and saves fresh data. Notes: *"Using live data from Trino (refreshed just now)"*
- **File fresh (< 24 hours old)** → AI uses cached file, no mention

### What the Trino query fetches

For each WixPayments-connected Base44 app (excluding Wix/Base44 internal accounts and already-reviewed apps):

| Field | Source |
|---|---|
| `app_id`, `app_url`, `msid` | `pp_base44_apps_replica` |
| `trino_app_name`, `trino_description` | `base44_apps` (latest by date) |
| `conversation_summary` | `base44_app_context_snapshots_mongo` (latest) |
| `manual_review_status` | `wp_rnc_review_replica` |
| `bo_link` | Constructed from msid |
| `payment_provider` | `uw_controls_intent_to_sell_population` |

### When Trino is unreachable

If the VPN is off or Trino returns an auth error, the AI warns once and continues with the last cached data. The cached file retains the last successful pull.

---

## 14. Base44 Platform Knowledge

### What is Base44?

Base44 is an AI-powered no-code platform that generates web applications using **React (Vite)** for the frontend with a managed backend (Deno serverless functions) and database (MongoDB-compatible entities).

### Key Architecture Facts for Scraping

| Aspect | Detail |
|--------|--------|
| Frontend | React SPA (Vite), client-side rendered |
| Domain patterns | `app.base44.com/app/{id}`, `{slug}.base44.app`, custom domains |
| Static HTML | Empty shell — `<div id="root">` + script tags. Standard HTML scraping returns nothing useful |
| Entities | MongoDB-compatible data models (products, orders, users, etc.) accessed via SDK |
| Backend functions | Deno-powered serverless, callable via `/functions/{name}` |
| Auth | Google, Email/password, Microsoft, Facebook, Apple, SSO |
| Payments | Stripe, Wix Payments integration |
| AI Agents | OpenAI, Google Gemini, Anthropic Claude |
| CDN | Supabase for images/assets |

### Collected Base44 Documentation

Stored in `docs/base44-docs/`:
- Payments (Wix, Stripe)
- Entities (data model)
- Login & registration
- Access control
- Backend functions
- AI agents & providers
- Security & privacy
- Responsible use policy
- Wix prohibited products list

---

## 13. Security & Compliance

- `.env` is in `.gitignore` — TRINO_HOST, OPENAI_API_KEY, cookies never committed
- Trino connection uses env config; optional SSO/cookie (no API key in repo)
- Pipeline reads only app_id, app_name, app_url, conversation_summary from input JSON
- Writes only to `output/` and `data/temp_uw/`
- No automated final decision with `--llm none` — conclusions state "Manual Review Required"
- With LLM, output is advisory and should be reviewed by a human
- Scraping uses only public endpoints and publicly accessible app URLs

---

## 16. Audit Checklist

### Data Flow
- [ ] App list loads (merged `real_apps.json` + `user_apps.json`)
- [ ] Look up by App ID / MSID / WP account ID returns one app
- [ ] Profile shows user_description, categories, public_settings (from Trino or full_profiles)
- [ ] Conversation shows messages or snapshots or placeholder with instructions
- [ ] `data/trino_full_population.json` exists and is less than 24 hours old

### UW Pipeline
- [ ] Cached UW verdict shows verdict, reasoning, non-compliant subcategories
- [ ] "Run UW for this app" writes conclusion and shows verdict after refresh
- [ ] `run_underwriting.py --llm none` produces conclusion with evidence block
- [ ] Policy step does not invent categories not present in app summary
- [ ] Batch "UW for missing" writes conclusions with manifest

### App Compliance Screener
- [ ] `python3 scripts/validate_screener.py` — reports ≥48% accuracy on known non-compliant apps
- [ ] Apps with no scraped content return "Insufficient Data" (gray), not "Likely Supportable"
- [ ] Auth-walled apps with revealing conversation summaries are flagged when `conversation_summary=` is passed
- [ ] ZazaStyle app (`699ce719af3b04ee2d440034`) → "Not Supportable" via `zaza` + `packman` keywords
- [ ] `python3 scripts/screen_with_trino.py --context-only` runs on 240 apps in under 2 seconds
- [ ] All screened results appear in `data/findings.jsonl`
- [ ] Analyst can update review status (Pending → Reviewed / Escalated / Approved / Declined)

### Scraping
- [ ] Base44 URLs: API + meta tags extracted in ~1-3s (fast mode)
- [ ] Deep scrape: Playwright renders page, captures API responses
- [ ] Auth wall detected and classified (private vs broken vs public)
- [ ] Custom domain Base44 detection works
- [ ] Non-Base44 URLs: urllib → Playwright fallback

### Data Persistence
- [ ] Fetch script writes `trino_*.json` and `full_profiles.json`
- [ ] With Trino off, profile and conversation populate from `full_profiles.json`
- [ ] App summary cache in `output/uw_cache/` reused on re-runs
- [ ] Trino auto-refresh rule fires at start of each Cursor session (checks file age)

---

*Generated from the Base44 Underwriting Lookup project. For setup help, see `docs/RUN-UW-APP-ON-MY-COMPUTER.md`.*
