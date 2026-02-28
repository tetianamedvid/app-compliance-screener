# Trino setup and troubleshooting

This document covers live Trino mode: configuration, testing, and common issues.

---

## Configuration (.env)

| Parameter | Required | Description |
|-----------|----------|-------------|
| `TRINO_HOST` | Yes (for live) | Trino coordinator host. **Not** the Quix web UI URL. |
| `TRINO_USE_LOCAL_ONLY` | No | Set to `0` for live Trino; `1` for local JSON only. |
| `TRINO_PORT` | No | Default `443`. |
| `TRINO_CATALOG` | No | Default `prod`. |
| `TRINO_USER` | No | Default `uw_app`. |
| `TRINO_COOKIE` | Optional | Browser session cookie when using Quix/SSO. |
| `TRINO_AUTH_HEADER` | Optional | Bearer token alternative to cookie. |
| `TRINO_USE_SSO` | No | `true` for OAuth2; `false` when using cookie/token. |

---

## Test live Trino connection

Run:

```bash
python3 scripts/test_live_trino.py
```

**What it does:**

1. **Connection test** — Runs `SELECT 1` against Trino. If this fails, you see the Trino error (e.g. 403 Forbidden).
2. **Resolve test** — Looks up a sample app by `app_id`.
3. **Full profile test** — Fetches full profile for that app.

**Example output when connection fails:**

```
Trino configured. Testing connection (SELECT 1)...
Connection: FAIL
Trino error: 403 Forbidden from bo.wix.com. That host is likely the Quix/data platform web UI (e.g. bo.wix.com), not the Trino API. Get the actual Trino coordinator host from Quix or your data platform docs and set TRINO_HOST to that.
```

**Example output when it works:**

```
Trino configured. Testing connection (SELECT 1)...
Connection: OK
Testing resolve by app_id...
Resolve: OK — <app_name>
Testing full profile...
Full profile: OK — <n> columns
Live Trino test passed.
```

---

## 403 Forbidden: wrong host (Wix / Quix)

**Symptom:** `test_live_trino.py` or the dashboard shows "403 Forbidden" and mentions `bo.wix.com` or Wix.

**Cause:** `TRINO_HOST=bo.wix.com` (or similar) points to the **Quix web UI**, not the Trino API. The web UI returns 403 for programmatic Trino requests.

**Fix:**

- **Do not use:** `bo.wix.com` or the URL you use to open Quix in the browser.
- **Do:** Get the **Trino coordinator host** from Quix or your data platform docs — e.g. `trino.…` or a "Trino connection" / "JDBC/ODBC endpoint" host.
- Set that host in `.env` as `TRINO_HOST=…`.

**Alternative (browser session):** If you can run queries in Quix in the browser, you can use `TRINO_COOKIE` with your session cookie so the app runs as you. See "Use your browser session" in `RUN-UW-APP-ON-MY-COMPUTER.md`. Note: cookies expire; you may need to refresh them.

---

## Debug: Trino conversation history

To inspect conversation history for an app **directly from Trino** (no local JSON):

```bash
python3 scripts/debug_trino_conversation.py <app_id>
```

Example:

```bash
python3 scripts/debug_trino_conversation.py 685b7fb87461017a0816baa3
```

**What it does:** Fetches from `prod.base44.base44_conversation_messages_mongo` (message-level) or `prod.marketing.base44_app_context_snapshots_mongo` (snapshots). Uses **only Trino** — no `full_profiles.json` or other local data.

**Requirements:** `TRINO_USE_LOCAL_ONLY=0`, `TRINO_HOST` set, Trino reachable.

See `docs/DEBUG-TRINO-CONVERSATION.md` for full details.

---

## Local-only mode (no Trino)

When you don't have a working Trino host or want to use only local data:

1. Set `TRINO_USE_LOCAL_ONLY=1` in `.env`.
2. Ensure `APPS_JSON_PATH` points to your app list (e.g. `data/real_apps.json`).
3. Optionally add `data/full_profiles.json` for extended profile and conversation data.

The app will use only local JSON; no Trino connection is attempted.

---

## Summary

| Goal | Action |
|------|--------|
| Use live Trino | Set `TRINO_USE_LOCAL_ONLY=0`, `TRINO_HOST` to the **actual Trino coordinator host** (not bo.wix.com). |
| Verify connection | Run `python3 scripts/test_live_trino.py`. |
| Debug conversation | Run `python3 scripts/debug_trino_conversation.py <app_id>`. |
| Use local data only | Set `TRINO_USE_LOCAL_ONLY=1`. |
