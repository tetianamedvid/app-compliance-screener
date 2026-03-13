#!/usr/bin/env python3
"""
Debug tool: show conversation history for an app_id fetched directly from Trino.
Uses ONLY Trino — no local JSON (full_profiles, real_apps, etc.).

Usage:
  python3 scripts/debug_trino_conversation.py <app_id>

Example:
  python3 scripts/debug_trino_conversation.py 685b7fb87461017a0816baa3
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
except ImportError:
    pass

from uw_app.trino_client import (
    get_conversation_messages,
    get_conversation_snapshots,
    is_configured,
    get_last_trino_error,
)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/debug_trino_conversation.py <app_id>")
        sys.exit(1)

    app_id = sys.argv[1].strip()
    if not app_id:
        print("Error: app_id is empty")
        sys.exit(1)

    if not is_configured():
        print("Error: Trino not configured. Set TRINO_HOST in .env and ensure TRINO_USE_LOCAL_ONLY is not 1.")
        sys.exit(1)

    print(f"Fetching conversation from Trino for app_id: {app_id}")
    print("(Source: prod.base44.base44_conversation_messages_mongo, prod.marketing.base44_app_context_snapshots_mongo)")
    print("-" * 60)

    # 1) Message-level data (base44_conversation_messages_mongo)
    # get_conversation_messages uses messages table first, falls back to snapshots if empty
    messages = get_conversation_messages(app_id)

    if not messages:
        err = get_last_trino_error()
        print("No conversation data returned from Trino.")
        if err:
            print(f"Last Trino error: {err}")
        else:
            print("(No error logged — table may simply have no rows for this app_id)")
        sys.exit(1)

    # Detect source: message-level has "role" key
    is_message_level = "role" in (messages[0] if messages else {})

    if is_message_level:
        print(f"Messages ({len(messages)} rows from base44_conversation_messages_mongo):\n")
        for i, m in enumerate(messages, 1):
            ts = m.get("created_at", "—")
            role = m.get("role", "—")
            content = (m.get("content") or "")[:500]
            if len((m.get("content") or "")) > 500:
                content += "…"
            print(f"  [{i}] {ts} | {role}")
            print(f"      {content}")
            print()
    else:
        print(f"Snapshots ({len(messages)} rows from base44_app_context_snapshots_mongo):\n")
        for i, s in enumerate(messages, 1):
            ts = s.get("created_at", "—")
            content = (s.get("content") or "")[:800]
            if len((s.get("content") or "")) > 800:
                content += "…"
            print(f"  [{i}] {ts}")
            print(f"      {content}")
            print()

    print("-" * 60)
    print(f"Done. Total: {len(messages)} items from Trino.")


if __name__ == "__main__":
    main()
