#!/usr/bin/env python3
"""
Batch-screen all non-flagged apps and surface any that the policy classifier flags.

Reads data/Non-flagged apps - Sheet1.csv, runs the screener in parallel,
then prints a report: flagged first (red/orange), then insufficient data, then clean.

Usage:
    python3 scripts/screen_non_flagged.py
    python3 scripts/screen_non_flagged.py --workers 8   # more parallelism
    python3 scripts/screen_non_flagged.py --out output/non_flagged_screen.csv
"""
import csv
import sys
import argparse
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from uw_app.app_screener import screen_batch, ScreenResult

NON_FLAGGED_CSV = ROOT / "data" / "Non-flagged apps - Sheet1.csv"
DEFAULT_OUT = ROOT / "output" / "non_flagged_screen_results.csv"


def load_non_flagged() -> list[dict]:
    rows = []
    with open(NON_FLAGGED_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("app_url") or "").strip()
            app_id = (row.get("app_id") or "").strip()
            if url.startswith("http"):
                rows.append({"url": url, "app_id": app_id})
    return rows


def save_csv(results: list[ScreenResult], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "overall_verdict", "overall_color", "confidence",
        "url", "app_id", "app_name", "app_description",
        "top_category", "top_subcategory",
        "entity_types", "payment_signals",
        "elapsed_seconds", "screened_at", "error",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            row = {
                "overall_verdict": r.overall_verdict,
                "overall_color": r.overall_color,
                "confidence": r.confidence,
                "url": r.url,
                "app_id": r.app_id or "",
                "app_name": r.app_name or "",
                "app_description": (r.app_description or "")[:200],
                "top_category": r.top_category,
                "top_subcategory": r.top_subcategory,
                "entity_types": ", ".join(r.entity_types),
                "payment_signals": ", ".join(r.payment_signals),
                "elapsed_seconds": r.elapsed_seconds,
                "screened_at": r.screened_at,
                "error": r.error or "",
            }
            w.writerow(row)
    print(f"\nResults saved to: {out_path}")


def color_label(color: str) -> str:
    return {"red": "🔴", "orange": "🟠", "green": "🟢", "gray": "⚫"}.get(color, "?")


def main():
    parser = argparse.ArgumentParser(description="Batch-screen non-flagged apps")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers (default 6)")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT), help="Output CSV path")
    args = parser.parse_args()

    apps = load_non_flagged()
    if not apps:
        print("No apps found in CSV. Check the file path.")
        sys.exit(1)

    print(f"Screening {len(apps)} non-flagged apps with {args.workers} workers...")
    print(f"Started: {datetime.now().strftime('%H:%M:%S')}\n")

    urls = [a["url"] for a in apps]
    results = screen_batch(urls, max_workers=args.workers)

    # Sort: red first, then orange, then gray, then green; within each group by confidence desc
    _order = {"red": 0, "orange": 1, "gray": 2, "green": 3}
    results.sort(key=lambda r: (_order.get(r.overall_color, 9), -r.confidence))

    suspicious = [r for r in results if r.overall_color in ("red", "orange")]
    no_data = [r for r in results if r.overall_color == "gray"]
    clean = [r for r in results if r.overall_color == "green"]
    errors = [r for r in results if r.error and r.overall_color == "gray"]

    print("=" * 70)
    print(f"RESULTS: {len(suspicious)} flagged  |  {len(clean)} clean  |  {len(no_data)} insufficient data")
    print("=" * 70)

    if suspicious:
        print(f"\n🚨 FLAGGED — need review ({len(suspicious)} apps):\n")
        for r in suspicious:
            top = r.policy_matches[0] if r.policy_matches else {}
            print(f"  {color_label(r.overall_color)} {r.overall_verdict} ({r.confidence}%)")
            print(f"     URL : {r.url}")
            if r.app_name:
                print(f"     Name: {r.app_name}")
            if r.app_description:
                print(f"     Desc: {r.app_description[:120]}")
            if top:
                kw = top.get("keywords", [])[:5]
                print(f"     Hit : {top.get('category')} / {top.get('subcategory')}")
                print(f"     KW  : {kw}")
            print()

    if no_data:
        print(f"\n⚠️  INSUFFICIENT DATA — could not scrape ({len(no_data)} apps):\n")
        for r in no_data:
            err = f" [{r.error[:60]}]" if r.error else ""
            print(f"  ⚫ {r.url}{err}")

    print(f"\n✅ CLEAN — no policy flags ({len(clean)} apps)")

    # Save CSV
    save_csv(results, Path(args.out))

    print(f"\nDone: {datetime.now().strftime('%H:%M:%S')}")
    print(f"Total screened: {len(results)}")
    print(f"Flagged: {len(suspicious)} | Clean: {len(clean)} | No data: {len(no_data)}")


if __name__ == "__main__":
    main()
