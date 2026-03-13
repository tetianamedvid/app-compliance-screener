#!/usr/bin/env python3
"""
Run all validations and optional sync steps for the UW app and data.
  1. Validate: local app list count vs Trino WP-connected count (data full).
  2. Optional --fetch-profiles: fetch user logs + conversations from Trino, build full_profiles.json.
  3. Optional --uw-missing: run underwriting for apps that have no conclusion in run_uw_lookup.

Usage:
  python3 scripts/run_all.py                    # validate only
  python3 scripts/run_all.py --fetch-profiles   # validate + refresh full_profiles from Trino
  python3 scripts/run_all.py --uw-missing      # validate + run UW for apps missing conclusions
  python3 scripts/run_all.py --qa              # validate + QA (resolve, profile, Trino full profile)
  python3 scripts/run_all.py --fetch-profiles --uw-missing --force   # all steps; --force skips validation failure
"""
import argparse
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent


def main():
    p = argparse.ArgumentParser(description="Run validations and optional sync (fetch profiles, UW missing)")
    p.add_argument("--qa", action="store_true", help="Run QA: resolve + profile + Trino full profile (qa_uw_lookup.py)")
    p.add_argument("--fetch-profiles", action="store_true", help="Fetch Trino user logs + conversations and build full_profiles.json")
    p.add_argument("--uw-missing", action="store_true", help="Run underwriting for apps missing a conclusion in run_uw_lookup")
    p.add_argument("--uw-scrape", action="store_true", help="When running UW for missing, scrape app URLs (default: no scrape)")
    p.add_argument("--force", action="store_true", help="Continue even if WP app list validation fails (data not full)")
    args = p.parse_args()

    # 1. Validate WP app list
    r = subprocess.run([sys.executable, str(root / "scripts" / "validate_wp_app_list.py")], cwd=str(root))
    if r.returncode != 0 and not args.force:
        print("Stopping: fix app list (run merge_trino_wp_apps_into_real.py) or use --force.")
        return r.returncode
    if r.returncode != 0:
        print("Validation failed but continuing (--force).")

    # 2. Optional: QA (resolve + profile + Trino)
    if args.qa:
        r_qa = subprocess.run([sys.executable, str(root / "scripts" / "qa_uw_lookup.py")], cwd=str(root))
        if r_qa.returncode != 0:
            return r_qa.returncode

    # 3. Optional: fetch full profiles from Trino
    if args.fetch_profiles:
        r2 = subprocess.run([sys.executable, str(root / "scripts" / "fetch_full_profiles_from_trino.py")], cwd=str(root))
        if r2.returncode != 0:
            return r2.returncode

    # 4. Optional: run UW for missing apps
    if args.uw_missing:
        cmd_uw = [sys.executable, str(root / "scripts" / "run_uw_for_missing.py")]
        if not args.uw_scrape:
            cmd_uw.append("--no-scrape")
        r3 = subprocess.run(cmd_uw, cwd=str(root))
        if r3.returncode != 0:
            return r3.returncode

    print("run_all done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
