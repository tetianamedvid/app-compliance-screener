#!/usr/bin/env python3
"""
Test live Trino connection. Run after setting .env (TRINO_HOST, etc.) and connecting to VPN.
  python3 scripts/test_live_trino.py
"""
import sys
from pathlib import Path

# Project root on path so uw_app can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from uw_app.trino_client import is_configured, test_connection, resolve, get_full_profile, get_last_trino_error

def main():
    if not is_configured():
        print("TRINO_HOST is not set in .env. Copy .env.example to .env, set TRINO_HOST, and connect to VPN.")
        sys.exit(1)
    print("Trino configured. Testing connection (SELECT 1)...")
    if not test_connection():
        err = get_last_trino_error()
        print("Connection: FAIL")
        if err:
            print("Trino error:", err)
        else:
            print("(No error — TRINO_USE_LOCAL_ONLY may be 1, or TRINO_HOST unset)")
        sys.exit(1)
    print("Connection: OK")
    print("Testing resolve by app_id...")
    r = resolve("app_id", "698406273ade17b9bd851188")
    if not r:
        err = get_last_trino_error()
        print("Resolve: FAIL (no row returned)")
        if err:
            print("Trino error:", err)
        sys.exit(1)
    print("Resolve: OK —", r.get("app_name"))
    print("Testing full profile...")
    p = get_full_profile(r.get("app_id"))
    if not p:
        print("Full profile: FAIL (no row returned)")
        sys.exit(1)
    print("Full profile: OK —", len(p), "columns")
    print("Live Trino test passed.")

if __name__ == "__main__":
    main()
