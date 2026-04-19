#!/usr/bin/env python3
"""
Regression test: Nastia's manual review of 85 flagged cases (April 2026).

Each case was originally flagged by the classifier. Nastia reviewed and labelled:
  TRUE  = correctly flagged (true positive)
  FALSE = incorrectly flagged (false positive)

This test verifies:
  1. True positives are still detected  (TP retention)
  2. False positives are now suppressed  (FP reduction)

Usage:
    python3 scripts/test_nastia_review.py
    python3 scripts/test_nastia_review.py --verbose
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from uw_app.policy_classifier import classify

DATASET = ROOT / "data" / "nastia_review_dataset.json"


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    with open(DATASET) as f:
        cases = json.load(f)

    tp_kept = 0
    tp_lost = 0
    fp_fixed = 0
    fp_remain = 0
    tp_lost_items = []
    fp_fixed_items = []
    fp_remain_items = []

    for c in cases:
        text = " ".join(filter(None, [
            c.get("trino_app_name", ""),
            c.get("conversation_summary", ""),
            c.get("comments", ""),
        ]))
        if not text.strip():
            continue

        r = classify(text)
        still_flagged = r.overall_verdict not in ("Likely Supportable",)

        if c["human_label"]:  # TRUE = correctly flagged
            if still_flagged:
                tp_kept += 1
            else:
                tp_lost += 1
                tp_lost_items.append(c)
        else:  # FALSE = false positive
            if still_flagged:
                fp_remain += 1
                fp_remain_items.append({**c, "_verdict": r.overall_verdict, "_kw": [m.matched_keywords for m in r.matches]})
            else:
                fp_fixed += 1
                fp_fixed_items.append(c)

    total_tp = tp_kept + tp_lost
    total_fp = fp_fixed + fp_remain
    tp_rate = tp_kept / total_tp * 100 if total_tp else 0
    fp_fix_rate = fp_fixed / total_fp * 100 if total_fp else 0

    print("=" * 60)
    print("Nastia Review Regression Test")
    print("=" * 60)
    print(f"True Positives retained: {tp_kept}/{total_tp} ({tp_rate:.0f}%)")
    print(f"False Positives fixed:   {fp_fixed}/{total_fp} ({fp_fix_rate:.0f}%)")
    print(f"False Positives remain:  {fp_remain}/{total_fp} ({100-fp_fix_rate:.0f}%)")
    print("=" * 60)

    if verbose:
        if tp_lost_items:
            print(f"\nTP LOST ({tp_lost}):")
            for c in tp_lost_items:
                print(f"  - {c['trino_app_name']} [{c['top_subcategory']}]")

        if fp_fixed_items:
            print(f"\nFP FIXED ({fp_fixed}):")
            for c in fp_fixed_items:
                print(f"  + {c['trino_app_name']} [{c['top_subcategory']}]")

        if fp_remain_items:
            print(f"\nFP REMAINING ({fp_remain}):")
            for c in fp_remain_items:
                print(f"  ! {c['trino_app_name']} [{c['top_subcategory']}] => {c['_verdict']}, kw={c['_kw']}")

    if tp_rate < 80:
        print(f"\nFAIL: TP retention {tp_rate:.0f}% is below 80%")
        sys.exit(1)
    else:
        print(f"\nPASS: TP retention {tp_rate:.0f}% meets 80% threshold")
        sys.exit(0)


if __name__ == "__main__":
    main()
