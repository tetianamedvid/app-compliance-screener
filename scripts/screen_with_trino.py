#!/usr/bin/env python3
"""
Batch-screen all apps from Trino, using both URL scraping AND
conversation_summary as classifier input.

Catches ZazaStyle-type apps: auth-walled, fake description,
but conversation summary reveals the real product.

Usage:
    python3 scripts/screen_with_trino.py
    python3 scripts/screen_with_trino.py --workers 8
    python3 scripts/screen_with_trino.py --context-only   # classify conversation only, no scraping
"""
import csv
import json
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

from uw_app.policy_classifier import classify
from uw_app.app_screener import screen_batch, ScreenResult

TRINO_JSON = ROOT / "data" / "trino_full_population.json"
OUT_CSV = ROOT / "output" / "trino_screen_results.csv"


def load_trino_apps() -> list[dict]:
    with open(TRINO_JSON, encoding="utf-8") as f:
        return json.load(f)


def context_only_classify(apps: list[dict]) -> list[dict]:
    """
    Classify using ONLY conversation_summary + description + app name.
    No URL scraping. Very fast (~1s total).
    Used to find obvious violations hidden in Trino data.
    """
    results = []
    for app in apps:
        text = " ".join(filter(None, [
            app.get("trino_app_name") or "",
            app.get("trino_description") or "",
            app.get("conversation_summary") or "",
        ]))
        c = classify(text)
        results.append({
            "app_id": app.get("app_id"),
            "app_url": app.get("app_url"),
            "trino_app_name": app.get("trino_app_name"),
            "trino_description": (app.get("trino_description") or "")[:200],
            "conversation_summary": (app.get("conversation_summary") or "")[:300],
            "bo_link": app.get("bo_link"),
            "msid": app.get("msid"),
            "payment_provider": app.get("payment_provider"),
            "overall_verdict": c.overall_verdict,
            "overall_color": c.overall_color,
            "confidence": c.confidence,
            "top_category": c.top_match.category if c.top_match else "",
            "top_subcategory": c.top_match.subcategory if c.top_match else "",
            "matched_keywords": str(c.top_match.matched_keywords[:5]) if c.top_match else "",
        })
    return results


def color_label(color: str) -> str:
    return {"red": "🔴", "orange": "🟠", "green": "🟢", "gray": "⚫"}.get(color, "?")


def print_results(results: list[dict], mode: str):
    suspicious = [r for r in results if r["overall_color"] in ("red", "orange")]
    clean = [r for r in results if r["overall_color"] == "green"]
    no_data = [r for r in results if r["overall_color"] == "gray"]

    print("=" * 72)
    print(f"RESULTS ({mode}): {len(suspicious)} flagged  |  {len(clean)} clean  |  {len(no_data)} no data")
    print("=" * 72)

    if suspicious:
        # Sort: red first, then by confidence desc
        suspicious.sort(key=lambda r: (0 if r["overall_color"] == "red" else 1, -r["confidence"]))
        print(f"\n🚨 FLAGGED — need review ({len(suspicious)}):\n")
        for r in suspicious:
            print(f"  {color_label(r['overall_color'])} {r['overall_verdict']} ({r['confidence']}%)")
            print(f"     Name   : {r.get('trino_app_name') or r.get('app_name', '')}")
            print(f"     URL    : {r.get('app_url', '')}")
            print(f"     BO     : {r.get('bo_link', '')}")
            if r.get("top_category"):
                print(f"     Hit    : {r['top_category']} / {r['top_subcategory']}")
                print(f"     KW     : {r.get('matched_keywords', '')}")
            conv = r.get("conversation_summary", "")
            if conv:
                print(f"     Conv   : {conv[:120]}...")
            print()
    else:
        print("\n✅ No flags found.\n")


def save_csv(results: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not results:
        return
    fields = list(results[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"\nSaved to: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--context-only", action="store_true",
                        help="Classify Trino text only — no URL scraping (instant)")
    args = parser.parse_args()

    apps = load_trino_apps()
    print(f"Loaded {len(apps)} apps from Trino data")
    print(f"Started: {datetime.now().strftime('%H:%M:%S')}\n")

    if args.context_only:
        print("Mode: CONTEXT-ONLY (no scraping) — classifying name + description + conversation\n")
        results = context_only_classify(apps)
        print_results(results, "context-only")
        save_csv(results, ROOT / "output" / "trino_context_only_flags.csv")
    else:
        print(f"Mode: FULL (scraping + Trino context) — {args.workers} workers\n")
        urls = [a["app_url"] for a in apps if a.get("app_url")]
        trino_rows = [
            {
                "url": a["app_url"],
                "conversation_summary": a.get("conversation_summary") or "",
                "trino_description": a.get("trino_description") or "",
                "app_name_hint": a.get("trino_app_name") or "",
            }
            for a in apps if a.get("app_url")
        ]
        screen_results: list[ScreenResult] = screen_batch(urls, max_workers=args.workers, trino_rows=trino_rows)

        # Enrich with Trino metadata for display
        trino_by_url = {a["app_url"]: a for a in apps if a.get("app_url")}
        results = []
        for r in screen_results:
            meta = trino_by_url.get(r.url, {})
            results.append({
                "app_id": r.app_id or meta.get("app_id", ""),
                "app_url": r.url,
                "trino_app_name": meta.get("trino_app_name", ""),
                "app_name": r.app_name or "",
                "trino_description": (meta.get("trino_description") or "")[:200],
                "app_description": (r.app_description or "")[:200],
                "conversation_summary": (meta.get("conversation_summary") or "")[:300],
                "bo_link": meta.get("bo_link", ""),
                "msid": meta.get("msid", ""),
                "payment_provider": meta.get("payment_provider", ""),
                "overall_verdict": r.overall_verdict,
                "overall_color": r.overall_color,
                "confidence": r.confidence,
                "top_category": r.top_category,
                "top_subcategory": r.top_subcategory,
                "matched_keywords": str([m["keywords"][:4] for m in r.policy_matches[:1]]) if r.policy_matches else "",
                "content_length": r.content_length,
                "elapsed_seconds": r.elapsed_seconds,
                "error": r.error or "",
            })

        print_results(results, "scraping + Trino context")
        save_csv(results, OUT_CSV)

    print(f"\nDone: {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
