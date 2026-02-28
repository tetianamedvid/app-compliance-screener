#!/usr/bin/env python3
"""
Automated deep scraping tool for Base44 app URLs.

Opens each URL with a headless browser (Playwright), reads full rendered HTML,
follows internal links, collects content from all pages, and summarizes:
  - date_app_created
  - app_content
  - items_sold (list with name, description, price)

Usage:
  python3 scripts/scrape_app_deep.py --url "https://example.base44.app"
  python3 scripts/scrape_app_deep.py --apps data/real_apps.json --out output/scraped
  python3 scripts/scrape_app_deep.py --apps data/real_apps.json --max-pages 10 --delay 2

Requires: pip install playwright beautifulsoup4
          playwright install chromium
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent


def _get_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        print("Error: playwright not installed. Run: pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)


def _get_bs4():
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup
    except ImportError:
        print("Error: beautifulsoup4 not installed. Run: pip install beautifulsoup4", file=sys.stderr)
        sys.exit(1)


def get_rendered_html(url: str, timeout_ms: int = 15000) -> Optional[str]:
    """Load URL with Playwright and return full rendered HTML."""
    sync_playwright = _get_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1500)  # allow JS to render
            html = page.content()
            return html
        except Exception as e:
            print(f"  Warn: {url} — {e}", file=sys.stderr)
            return None
        finally:
            browser.close()


def extract_internal_links(html: str, base_url: str) -> list[str]:
    """Extract same-origin links from HTML."""
    BeautifulSoup = _get_bs4()
    soup = BeautifulSoup(html, "html.parser")
    base_parsed = urlparse(base_url)
    base_domain = base_parsed.netloc
    base_scheme = base_parsed.scheme or "https"
    links = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.netloc != base_domain:
            continue
        if not full.startswith("http"):
            full = f"{base_scheme}://{base_domain}{full}"
        links.append(full)
    return list(dict.fromkeys(links))


def extract_date_from_html(html: str) -> Optional[str]:
    """Extract date from meta tags, JSON-LD, or page content."""
    # Meta tags
    for pattern in [
        r'<meta[^>]*property=["\'](?:article:published_time|og:created_time|datePublished)["\'][^>]*content=["\']([^"\']+)["\']',
        r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\'](?:article:published_time|og:created_time)["\']',
        r'<meta[^>]*name=["\'](?:date|published)["\'][^>]*content=["\']([^"\']+)["\']',
    ]:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return _normalize_date(m.group(1))

    # JSON-LD
    for script in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(script.group(1).strip())
            if isinstance(data, dict):
                for key in ("datePublished", "dateCreated", "date"):
                    if key in data and data[key]:
                        return _normalize_date(str(data[key]))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        for key in ("datePublished", "dateCreated", "date"):
                            if key in item and item[key]:
                                return _normalize_date(str(item[key]))
        except json.JSONDecodeError:
            pass

    return None


def _normalize_date(s: str) -> str:
    """Normalize date string to YYYY-MM-DD."""
    s = str(s).strip()[:19]
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10], fmt[:len(s[:10])]).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s[:10] if s else ""


def extract_products_from_jsonld(html: str) -> list[dict]:
    """Extract product/offer info from JSON-LD."""
    products = []
    for script in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(script.group(1).strip())
            if isinstance(data, dict):
                _add_product_from_ld(products, data)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        _add_product_from_ld(products, item)
        except json.JSONDecodeError:
            pass
    return products


def _add_product_from_ld(products: list, data: dict):
    t = (data.get("@type") or "").lower()
    if "product" in t:
        p = {
            "name": data.get("name") or data.get("title") or "",
            "description": data.get("description") or "",
            "price": data.get("offers", {}).get("price") if isinstance(data.get("offers"), dict) else data.get("price") or "",
        }
        if isinstance(data.get("offers"), dict):
            p["price"] = data["offers"].get("price") or p.get("price") or ""
        if p.get("name") or p.get("description"):
            products.append(p)
    if "itemlistelement" in data or "itemListElement" in data:
        for item in data.get("itemListElement", data.get("itemlistelement", [])):
            if isinstance(item, dict):
                _add_product_from_ld(products, item)


def extract_products_from_dom(html: str) -> list[dict]:
    """Heuristic extraction of product-like elements from DOM."""
    BeautifulSoup = _get_bs4()
    soup = BeautifulSoup(html, "html.parser")
    products = []
    # Common patterns: [data-product], .product, .product-card, [itemtype*="Product"]
    for el in soup.find_all(attrs={"data-product": True}):
        name = el.get("data-product-name") or el.get("data-name") or ""
        desc = el.get("data-product-description") or el.get("data-description") or ""
        price = el.get("data-product-price") or el.get("data-price") or ""
        if name or desc:
            products.append({"name": name, "description": desc, "price": price})

    for el in soup.find_all(class_=re.compile(r"product|item|card")):
        name_el = el.find(class_=re.compile(r"name|title|product-name"))
        desc_el = el.find(class_=re.compile(r"description|desc"))
        price_el = el.find(class_=re.compile(r"price|cost"))
        name = (name_el.get_text() if name_el else "").strip()[:200]
        desc = (desc_el.get_text() if desc_el else "").strip()[:500]
        price = (price_el.get_text() if price_el else "").strip()[:50]
        if name or desc:
            products.append({"name": name, "description": desc, "price": price})

    return products


def extract_main_text(html: str, max_chars: int = 50000) -> str:
    """Extract main text from HTML (strip scripts, styles, tags)."""
    raw = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return (raw[:max_chars] + "...") if len(raw) > max_chars else raw if raw else ""


def scrape_page(url: str, delay_seconds: float = 1.0) -> Optional[dict]:
    """Scrape a single page: HTML, text, date, products."""
    import time
    html = get_rendered_html(url)
    if not html:
        return None
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    return {
        "url": url,
        "html": html,
        "text": extract_main_text(html),
        "date": extract_date_from_html(html),
        "products_jsonld": extract_products_from_jsonld(html),
        "products_dom": extract_products_from_dom(html),
    }


def scrape_app_deep(
    url: str,
    max_pages: int = 20,
    delay_seconds: float = 1.0,
) -> dict:
    """
    Scrape main URL and follow internal links. Collect all content and summarize.
    """
    result = {
        "app_url": url,
        "scraped_at": datetime.now().isoformat(),
        "date_app_created": None,
        "app_content": "",
        "items_sold": [],
        "linked_pages": [],
        "errors": [],
    }

    # Main page
    main = scrape_page(url, delay_seconds=0)
    if not main:
        result["errors"].append(f"Failed to load main page: {url}")
        return result

    result["app_content"] = main["text"]
    result["date_app_created"] = main["date"]
    all_products = list(main["products_jsonld"]) + list(main["products_dom"])

    # Merge products, dedupe by name
    seen_names = set()
    for p in all_products:
        name = (p.get("name") or "").strip()
        if name and name not in seen_names:
            seen_names.add(name)
            result["items_sold"].append({
                "name": name,
                "description": (p.get("description") or "").strip()[:1000],
                "price": str(p.get("price") or "").strip()[:100],
            })

    # Follow links
    links = extract_internal_links(main["html"], url)
    visited = {url}
    for link in links[:max_pages - 1]:
        if link in visited:
            continue
        visited.add(link)
        page_data = scrape_page(link, delay_seconds)
        if not page_data:
            continue
        result["linked_pages"].append({
            "url": link,
            "content_preview": (page_data["text"] or "")[:500],
        })
        if page_data["date"] and not result["date_app_created"]:
            result["date_app_created"] = page_data["date"]
        for p in page_data["products_jsonld"] + page_data["products_dom"]:
            name = (p.get("name") or "").strip()
            if name and name not in seen_names:
                seen_names.add(name)
                result["items_sold"].append({
                    "name": name,
                    "description": (p.get("description") or "").strip()[:1000],
                    "price": str(p.get("price") or "").strip()[:100],
                })

    return result


def write_summary(result: dict, out_path: Path, format: str = "both"):
    """Write summary as JSON and/or Markdown."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if format in ("json", "both"):
        json_path = out_path.with_suffix(".json")
        json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if format in ("md", "markdown", "both"):
        md_path = out_path.with_suffix(".md")
        md = _build_markdown(result)
        md_path.write_text(md, encoding="utf-8")


def _build_markdown(result: dict) -> str:
    lines = [
        "---",
        f"date_app_created: {result.get('date_app_created') or 'N/A'}",
        f"app_url: {result.get('app_url')}",
        f"scraped_at: {result.get('scraped_at')}",
        "---",
        "",
        "## App content",
        "",
        (result.get("app_content") or "(No content extracted)")[:30000],
        "",
        "## Items sold",
        "",
    ]
    items = result.get("items_sold") or []
    if not items:
        lines.append("(No items detected)")
    else:
        for i, item in enumerate(items, 1):
            name = item.get("name") or "Item"
            desc = item.get("description") or ""
            price = item.get("price") or ""
            lines.append(f"### {i}. {name}")
            if price:
                lines.append(f"**Price:** {price}")
            if desc:
                lines.append(f"**Description:** {desc}")
            lines.append("")

    lines.append("## Linked pages")
    linked = result.get("linked_pages") or []
    if not linked:
        lines.append("(None)")
    else:
        for lp in linked:
            lines.append(f"- {lp.get('url', '')}: {lp.get('content_preview', '')[:200]}...")
    return "\n".join(lines)


def load_apps(path: Path) -> list[dict]:
    """Load app list from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return [data]


def main():
    p = argparse.ArgumentParser(description="Deep scrape Base44 app URLs")
    p.add_argument("--url", help="Single URL to scrape")
    p.add_argument("--apps", type=Path, help="JSON app list (app_id, app_name, app_url)")
    p.add_argument("--out", type=Path, default=ROOT / "output" / "scraped", help="Output directory")
    p.add_argument("--max-pages", type=int, default=20, help="Max linked pages per app (default 20)")
    p.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds (default 1)")
    p.add_argument("--format", choices=("json", "md", "both"), default="both", help="Output format")
    args = p.parse_args()

    apps = []
    if args.url:
        apps.append({"app_id": "single", "app_name": "Single", "app_url": args.url})
    elif args.apps and args.apps.exists():
        apps = load_apps(args.apps)
    else:
        print("Error: provide --url or --apps with path to JSON", file=sys.stderr)
        sys.exit(1)

    args.out.mkdir(parents=True, exist_ok=True)
    for i, app in enumerate(apps):
        url = app.get("app_url")
        if not url or not url.startswith("http"):
            print(f"  Skip {app.get('app_id')}: no valid app_url")
            continue
        app_id = app.get("app_id") or f"app_{i}"
        app_name = app.get("app_name") or "Unknown"
        print(f"[{i+1}/{len(apps)}] Scraping {app_id} ({app_name})...")
        result = scrape_app_deep(url, max_pages=args.max_pages, delay_seconds=args.delay)
        result["app_id"] = app_id
        result["app_name"] = app_name
        out_path = args.out / f"scraped_{app_id}"
        write_summary(result, out_path, format=args.format)
        print(f"  -> {out_path}.json, {out_path}.md")
    print("Done.")


if __name__ == "__main__":
    main()
