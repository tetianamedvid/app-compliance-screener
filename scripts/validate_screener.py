#!/usr/bin/env python3
"""
Validate screener accuracy: regression tests + analyst-reviewed CSV.

Two modes:
  1. Regression tests — hard-coded known-good cases (true positives + true negatives)
  2. CSV validation  — bulk-test known non-compliant apps from analyst CSV

Usage:
    python3 scripts/validate_screener.py              # run both
    python3 scripts/validate_screener.py --regression  # regression tests only
    python3 scripts/validate_screener.py --csv         # CSV validation only
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from uw_app.app_screener import screen

NON_COMPLIANT_CSV = ROOT / "data" / "non-compliant apps - Sheet1 (1).csv"

# ── Regression test cases ─────────────────────────────────────────────────────
# Each case: (url, expected_flagged: bool, expected_color_or_None, description)
#   expected_flagged: True = should be red/orange, False = should be green/gray
#   expected_color_or_None: if set, assert exact color; None = don't check exact color

REGRESSION_CASES: list[dict] = [
    # ── True negatives — should NOT be flagged ──
    {
        "url": "https://grumpy-eldoria-chronicles-rpg.base44.app",
        "expect_flagged": False,
        "expect_color": None,
        "description": "RPG game — was false-positive for Alcohol (keyword 'rum' in forum context)",
    },

    # Add more cases as you discover false positives or fix bugs:
    # {
    #     "url": "https://some-clean-app.base44.app",
    #     "expect_flagged": False,
    #     "expect_color": None,
    #     "description": "Clean e-commerce store — should not trigger any flags",
    # },
    # {
    #     "url": "https://known-bad-app.base44.app",
    #     "expect_flagged": True,
    #     "expect_color": "red",
    #     "description": "Known non-compliant app selling prohibited items",
    # },
]


def run_regression() -> tuple[int, int, list[dict]]:
    """Run hard-coded regression tests. Returns (passed, failed, failure_details)."""
    if not REGRESSION_CASES:
        print("No regression cases defined yet. Add cases to REGRESSION_CASES.\n")
        return 0, 0, []

    print(f"Running {len(REGRESSION_CASES)} regression tests...\n")
    print("-" * 70)

    passed = 0
    failed = 0
    failures: list[dict] = []

    for i, case in enumerate(REGRESSION_CASES, 1):
        url = case["url"]
        expect_flagged = case["expect_flagged"]
        expect_color = case.get("expect_color")
        desc = case.get("description", "")

        print(f"[{i}/{len(REGRESSION_CASES)}] {url}")
        print(f"  Desc: {desc}")

        try:
            result = screen(url)
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1
            failures.append({**case, "error": str(e)})
            print()
            continue

        is_flagged = result.overall_color in ("red", "orange")

        flag_ok = is_flagged == expect_flagged
        color_ok = (expect_color is None) or (result.overall_color == expect_color)
        ok = flag_ok and color_ok

        if ok:
            passed += 1
            status = "✓ PASS"
        else:
            failed += 1
            status = "✗ FAIL"
            reason = ""
            if not flag_ok:
                reason = f"expected {'flagged' if expect_flagged else 'clean'}, got {'flagged' if is_flagged else 'clean'}"
            elif not color_ok:
                reason = f"expected color={expect_color}, got {result.overall_color}"
            failures.append({**case, "actual_verdict": result.overall_verdict,
                             "actual_color": result.overall_color, "reason": reason})

        print(f"  Result: {status}  — {result.overall_verdict} ({result.overall_color}, {result.confidence}%)")
        if not ok:
            print(f"  Reason: {failures[-1].get('reason', '')}")
        if result.policy_matches:
            top = result.policy_matches[0]
            kws = top["keywords"][:4]
            print(f"  Top hit: {top['category']} / {top['subcategory']} — keywords: {kws}")
        print()

    print("=" * 70)
    print(f"REGRESSION: {passed} passed, {failed} failed out of {passed + failed}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f['url']}")
            print(f"    Expected: {'flagged' if f['expect_flagged'] else 'clean'}"
                  f"  Got: {f.get('actual_verdict', 'ERROR')}")
            if f.get("reason"):
                print(f"    Reason: {f['reason']}")
    print()
    return passed, failed, failures


# ── CSV validation (original bulk test) ───────────────────────────────────────

def load_non_compliant() -> list[dict]:
    if not NON_COMPLIANT_CSV.exists():
        return []
    rows = []
    with open(NON_COMPLIANT_CSV, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if len(row) >= 8:
                url = row[1].strip()
                decision = row[5].strip()
                reasoning = row[6].strip()
                app_id = row[7].strip()
                if url.startswith("http"):
                    rows.append({
                        "url": url,
                        "app_id": app_id,
                        "analyst_decision": decision,
                        "analyst_reasoning": reasoning,
                    })
    return rows


def run_csv_validation() -> tuple[int, int]:
    apps = load_non_compliant()
    if not apps:
        print("No apps found in CSV. Check the file path.")
        return 0, 0

    print(f"Testing {len(apps)} known non-compliant apps...\n")
    print("-" * 70)

    flagged = []
    missed = []

    for i, app in enumerate(apps, 1):
        print(f"[{i}/{len(apps)}] {app['url']}", flush=True)
        result = screen(app["url"])

        is_flagged = result.overall_color in ("red", "orange")

        if is_flagged:
            flagged.append((app, result))
            status = "✓ FLAGGED"
        else:
            missed.append((app, result))
            status = "✗ MISSED"

        print(f"  Status  : {status}")
        print(f"  Analyst : {app['analyst_decision']} — {app['analyst_reasoning'][:100]}")
        print(f"  Screener: {result.overall_verdict} (confidence: {result.confidence}%)")
        if result.policy_matches:
            top = result.policy_matches[0]
            kw = top["keywords"][:4]
            print(f"  Top hit : {top['category']} / {top['subcategory']} — keywords: {kw}")
        elif result.overall_color == "gray":
            print(f"  Note    : Insufficient data — could not scrape app content")
        print()

    total = len(apps)
    pct = round(100 * len(flagged) / total) if total else 0

    print("=" * 70)
    print("CSV ACCURACY SUMMARY")
    print(f"  Total apps tested : {total}")
    print(f"  Correctly flagged : {len(flagged)} ({pct}%)")
    print(f"  Missed            : {len(missed)} ({100 - pct}%)")
    print()

    if missed:
        print("MISSED APPS (screener did not flag):")
        for app, result in missed:
            print(f"  {result.overall_verdict:25s}  {app['url']}")
            print(f"    Analyst reason: {app['analyst_reasoning'][:90]}")
    print()

    return len(flagged), len(missed)


def main():
    parser = argparse.ArgumentParser(description="Validate screener accuracy")
    parser.add_argument("--regression", action="store_true",
                        help="Run regression tests only")
    parser.add_argument("--csv", action="store_true",
                        help="Run CSV validation only")
    args = parser.parse_args()

    run_both = not args.regression and not args.csv

    if args.regression or run_both:
        run_regression()

    if args.csv or run_both:
        run_csv_validation()


if __name__ == "__main__":
    main()
