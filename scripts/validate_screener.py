#!/usr/bin/env python3
"""
Validate screener accuracy against 36 known non-compliant apps.

Reads the analyst-reviewed non-compliant CSV, runs the screener on each URL,
and reports how many are correctly flagged vs missed.

Usage:
    python3 scripts/validate_screener.py
"""
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


def load_non_compliant() -> list[dict]:
    rows = []
    with open(NON_COMPLIANT_CSV, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)  # skip header
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


def main():
    apps = load_non_compliant()
    if not apps:
        print("No apps found in CSV. Check the file path.")
        sys.exit(1)

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
    print(f"ACCURACY SUMMARY")
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

    if flagged:
        print("CORRECTLY FLAGGED:")
        for app, result in flagged:
            top = result.policy_matches[0] if result.policy_matches else {}
            cat = f"{top.get('category','?')} / {top.get('subcategory','?')}" if top else "(verdict override)"
            print(f"  [{result.overall_color.upper():6s}] {app['url']}")
            print(f"    Screener: {result.overall_verdict} — {cat}")
            print(f"    Analyst : {app['analyst_reasoning'][:90]}")


if __name__ == "__main__":
    main()
