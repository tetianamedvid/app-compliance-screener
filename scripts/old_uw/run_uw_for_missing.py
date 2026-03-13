#!/usr/bin/env python3
"""
Run underwriting only for apps that do not yet have a conclusion in output/run_<run_id>/.
Loads app list from APPS_JSON_PATH (merged), finds app_ids with no conclusion_<app_id>.md,
writes a temp JSON and runs run_underwriting.py with it.
Run from project root.
  python3 scripts/run_uw_for_missing.py [--run-id uw_lookup] [--dry-run]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
except ImportError:
    pass

from uw_app.resolve import load_apps_index_merged

OUTPUT_DIR = root / "output"
RUN_SCRIPT = root / "run_underwriting.py"
POLICY_DEFAULT = root / "policy" / "policy-excerpt.txt"


def main():
    p = argparse.ArgumentParser(description="Run UW for apps missing a conclusion in the given run")
    p.add_argument("--run-id", default="uw_lookup", help="Run id (e.g. uw_lookup); conclusions in output/run_<run_id>/")
    p.add_argument("--dry-run", action="store_true", help="Only print missing app_ids and count")
    p.add_argument("--no-scrape", action="store_true", help="Do not scrape app URLs (default: scrape)")
    p.add_argument("--policy", type=Path, default=POLICY_DEFAULT, help="Policy file path")
    p.add_argument("--llm", default="none", choices=("auto", "openai", "ollama", "none"), help="LLM mode for run_underwriting")
    args = p.parse_args()

    by_app_id, _, _, _ = load_apps_index_merged()
    run_dir = OUTPUT_DIR / f"run_{args.run_id}"
    existing = set()
    if run_dir.exists():
        for f in run_dir.glob("conclusion_*.md"):
            # conclusion_<app_id>.md
            name = f.stem
            if name.startswith("conclusion_"):
                app_id = name[len("conclusion_"):].strip()
                if app_id:
                    existing.add(app_id)

    missing_ids = [aid for aid in by_app_id if aid not in existing]
    if not missing_ids:
        print("No missing apps — all", len(by_app_id), "apps already have a conclusion in", run_dir)
        return 0

    print("Missing conclusions for", len(missing_ids), "apps (total apps:", len(by_app_id), ", run:", args.run_id, ")")
    if args.dry_run:
        for aid in sorted(missing_ids)[:20]:
            print(" ", aid, by_app_id.get(aid, {}).get("app_name", ""))
        if len(missing_ids) > 20:
            print(" ... and", len(missing_ids) - 20, "more")
        return 0

    apps_list = [by_app_id[aid] for aid in missing_ids]
    temp_json = root / "data" / ".uw_missing_apps.json"
    temp_json.parent.mkdir(parents=True, exist_ok=True)
    temp_json.write_text(json.dumps(apps_list, indent=2, ensure_ascii=False), encoding="utf-8")

    cmd = [
        sys.executable,
        str(RUN_SCRIPT),
        "--apps", str(temp_json),
        "--policy", str(args.policy),
        "--out", str(OUTPUT_DIR),
        "--run-id", args.run_id,
        "--llm", args.llm,
    ]
    if args.no_scrape:
        cmd.append("--no-scrape")
    r = subprocess.run(cmd, cwd=str(root))
    if temp_json.exists():
        try:
            temp_json.unlink()
        except OSError:
            pass
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
