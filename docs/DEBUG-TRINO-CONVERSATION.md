# Debug: Trino conversation history

To inspect conversation history for an app **directly from Trino** (no local JSON):

## Usage

1. In **Terminal**, go to the project folder.
2. Run:
   ```bash
   python3 scripts/debug_trino_conversation.py <app_id>
   ```
3. Example:
   ```bash
   python3 scripts/debug_trino_conversation.py 685b7fb87461017a0816baa3
   ```

## What it does

Fetches conversation data from:
- `prod.base44.base44_conversation_messages_mongo` (message-level: role, content, created_date)
- `prod.marketing.base44_app_context_snapshots_mongo` (snapshots, if messages table is empty)

Uses **only Trino** — no `full_profiles.json` or other local conversation data.

## Requirements

- `TRINO_USE_LOCAL_ONLY=0` in `.env`
- `TRINO_HOST` set in `.env` (must be the **actual Trino coordinator host**, not bo.wix.com)
- Trino reachable (VPN if required)

**If you get no data or errors:** Run `python3 scripts/test_live_trino.py` to verify the connection. See `docs/TRINO-SETUP-AND-TROUBLESHOOTING.md`.
