#!/usr/bin/env python3
"""
Fast offline accuracy test: keyword classifier vs human-confirmed violations.

Uses data/confirmed_violations_parsed.json (no scraping, no API calls).
Run after any change to uw_app/policy_classifier.py to check for regressions.

Usage:
    python3 scripts/test_confirmed_violations.py
    python3 scripts/test_confirmed_violations.py --verbose
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from uw_app.policy_classifier import classify

VIOLATIONS_PATH = ROOT / "data" / "confirmed_violations_parsed.json"


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    with open(VIOLATIONS_PATH) as f:
        cases = json.load(f)

    flagged = 0
    missed = 0
    no_text = 0
    flagged_by_cat = Counter()
    missed_by_cat = defaultdict(list)
    category_total = Counter()

    for c in cases:
        cat = c["category"] or "Unknown"
        category_total[cat] += 1

        text = " ".join(filter(None, [
            c["app_name"], c["app_description"], c["conversation_summary"]
        ]))

        if not text.strip():
            no_text += 1
            continue

        cl = classify(text)
        is_flagged = cl.overall_verdict not in ("Likely Supportable",)

        if is_flagged:
            flagged += 1
            flagged_by_cat[cat] += 1
        else:
            missed += 1
            missed_by_cat[cat].append({
                "case": c["case_number"],
                "name": c["app_name"],
                "desc": (c["app_description"] or "")[:100],
            })

    total = flagged + missed
    rate = flagged / total * 100 if total else 0

    print("=" * 60)
    print(f"Confirmed Violations Accuracy: {flagged}/{total} ({rate:.0f}%)")
    print(f"No text (untestable): {no_text}")
    print("=" * 60)

    if verbose:
        print(f"\n{'Category':<40} {'Hit':>4} {'Tot':>4} {'Rate':>5}")
        print("-" * 60)
        for cat, tot in category_total.most_common():
            hit = flagged_by_cat.get(cat, 0)
            r = hit / tot * 100 if tot else 0
            flag = " ***" if r < 50 and tot >= 2 else ""
            print(f"{cat:<40} {hit:>4} {tot:>4} {r:>4.0f}%{flag}")

        if missed_by_cat:
            print(f"\nMissed ({missed}):")
            for cat, items in sorted(missed_by_cat.items(), key=lambda x: -len(x[1])):
                for m in items:
                    print(f"  [{cat}] {m['case']}: {m['name'] or '(no name)'}")

    if rate < 70:
        print(f"\nWARNING: Detection rate {rate:.0f}% is below 70% threshold")
        sys.exit(1)
    else:
        print(f"\nPASS: Detection rate {rate:.0f}% meets 70% threshold")
        sys.exit(0)


if __name__ == "__main__":
    main()
