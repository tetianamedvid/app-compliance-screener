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

from uw_app.trino_client import is_configured, resolve, get_full_profile

def main():
    if not is_configured():
        print("TRINO_HOST is not set in .env. Copy .env.example to .env, set TRINO_HOST, and connect to VPN.")
        sys.exit(1)
    print("Trino configured. Testing resolve by app_id...")
    r = resolve("app_id", "698406273ade17b9bd851188")
    if not r:
        print("Resolve: FAIL (no row returned)")
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
