#!/usr/bin/env python3
"""
First-run underwriting pipeline: load apps (from JSON/CSV), policy, call LLM, write conclusion per app.

No OpenAI key required: use --llm ollama (local Ollama) or --llm none (template-only). Default --llm auto tries OpenAI then Ollama then template.
Usage:
  python run_underwriting.py --apps data/sample_apps_from_trino.json --policy policy/policy-excerpt.txt --out output
  python run_underwriting.py --apps data/real_apps.json --policy policy/policy-excerpt.txt --out output --run-id my_run --llm none   # no API key
  python run_underwriting.py --apps data/real_apps.json --policy policy/policy-excerpt.txt --out output --run-id ollama_run --llm ollama   # needs Ollama running
Optional: OPENAI_API_KEY in .env for --llm openai or auto.
"""
from pathlib import Path
import argparse
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Load .env from project root so OPENAI_API_KEY can be set there (do not commit .env)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
PROMPT_TEMPLATE = (PROJECT_ROOT / "prompts" / "underwriting-conclusion.md").read_text()
MIDDLEMAN_TEMPLATE = (PROJECT_ROOT / "prompts" / "app-summary-middleman.md").read_text()
POLICY_COMPARISON_TEMPLATE = (PROJECT_ROOT / "prompts" / "policy-comparison.md").read_text()


def _extract_prompt_body(raw: str) -> str:
    start = raw.find("```") + 3
    end = raw.find("```", start)
    return raw[start:end].strip() if start > 2 and end > start else raw


def _get_prompt_body():
    return _extract_prompt_body(PROMPT_TEMPLATE)


def load_apps(path: Path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text())
    if path.suffix.lower() == ".csv":
        import csv
        rows = list(csv.DictReader(path.read_text().splitlines()))
        return rows
    raise ValueError(f"Unsupported format: {path.suffix}")


def load_policy(path: Path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".docx":
        try:
            from docx import Document
            doc = Document(path)
            return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip()).strip()
        except ImportError:
            raise SystemExit("Install python-docx to load .docx policy: pip install python-docx")
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _extract_meta_tags(html: str) -> str:
    """Extract SEO meta tags (title, description, og:*) from raw HTML — works even before JS renders."""
    if not html or len(html) < 50:
        return ""
    parts: list[str] = []
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if title_m:
        t = re.sub(r"\s+", " ", title_m.group(1)).strip()
        if t and len(t) > 2:
            parts.append(f"Title: {t}")
    meta_patterns = [
        ("description", r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']'),
        ("description", r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']'),
        ("og:title", r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']'),
        ("og:title", r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']'),
        ("og:description", r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']'),
        ("og:description", r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']'),
        ("og:image", r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']'),
        ("og:image", r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']'),
    ]
    seen_keys: set = set()
    for key, pattern in meta_patterns:
        if key in seen_keys:
            continue
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and len(val) > 2:
                parts.append(f"{key}: {val}")
                seen_keys.add(key)
    return " | ".join(parts) if parts else ""


def _fetch_sitemap(url: str, timeout: int = 5) -> list[str]:
    """Fetch sitemap.xml and return list of page URLs. Returns empty list on failure."""
    try:
        parsed = urlparse(url)
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        req = Request(sitemap_url, headers={"User-Agent": "Mozilla/5.0 (compatible; Base44-UW/1.0)"})
        with urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        if "<urlset" not in content.lower() and "<sitemapindex" not in content.lower():
            return []
        urls = re.findall(r"<loc>\s*(https?://[^<]+)\s*</loc>", content, re.IGNORECASE)
        return urls[:50]
    except Exception:
        return []


def _detect_base44_from_html(html: str) -> bool:
    """Detect if a page is a Base44 app from HTML markers (for custom domain detection)."""
    if not html:
        return False
    indicators = [
        "base44.app", "base44.com", "__BASE44__", "base44-apps.com",
        "base44Client", "@base44/sdk", "base44_",
    ]
    html_lower = html.lower()
    return sum(1 for i in indicators if i.lower() in html_lower) >= 2


def _fetch_frontend_config(url: str, timeout: int = 2) -> str:
    """Fetch /api/frontend-config.js and extract platform signals (integrations, auth, SDK config)."""
    try:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        config_url = f"{origin}/api/frontend-config.js"
        req = Request(config_url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        m = re.search(r'__APP_CONFIG__\s*=\s*({.+})', raw)
        if not m:
            return ""
        config = json.loads(m.group(1))
        parts: list[str] = []
        sdk_url = config.get("SDK_BACKEND_URL", "")
        if sdk_url:
            parts.append(f"SDK backend: {sdk_url}")
        apps_domain = config.get("APPS_BASE_DOMAIN", "")
        if apps_domain:
            parts.append(f"Apps domain: {apps_domain}")
        if config.get("TURNSTILE_ENABLED") == "true":
            parts.append("Anti-bot: Cloudflare Turnstile enabled")
        if config.get("GOOGLE_CLIENT_ID"):
            parts.append("Auth: Google login enabled")
        fiverr = config.get("FIVERR_SDK_CDN_PROD") or config.get("FIVERR_ENV")
        if fiverr:
            parts.append(f"Integration: Fiverr (env={config.get('FIVERR_ENV', '?')})")
        return "\n".join(parts) if parts else ""
    except Exception:
        return ""


def _scan_js_bundle_for_signals(url: str, timeout: int = 3) -> str:
    """Fetch the HTML shell, discover JS bundles, scan for app-specific API/entity/function references.
    Skips the shared platform framework (main.js) — only scans app-specific chunks."""
    try:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        scripts = re.findall(r'<script[^>]+src=["\']([^"\']+\.js)["\']', html, re.IGNORECASE)
        parts: list[str] = []

        # Identify app-specific chunks (skip framework, main, badge, google, tailwind)
        skip_names = ("framework.", "main.", "badge.", "google", "tailwind", "gsi/client")
        app_chunks = []
        for s in scripts:
            if not any(skip in s.lower() for skip in skip_names):
                full = s if s.startswith("http") else f"{origin}{s}"
                app_chunks.append(full)

        if not app_chunks:
            return ""

        # Only scan the first app-specific chunk (usually contains entity definitions)
        target = app_chunks[0]
        req2 = Request(target, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req2, timeout=timeout) as resp2:
            js = resp2.read().decode("utf-8", errors="replace")

        entity_patterns = set()
        for pattern in [
            r'entities/([a-z_][a-z0-9_]{2,30})',
            r'"entity"\s*:\s*"([a-z_][a-z0-9_]{2,30})"',
            r'entityName\s*:\s*["\']([a-z_][a-z0-9_]{2,30})["\']',
        ]:
            entity_patterns.update(re.findall(pattern, js, re.IGNORECASE))
        framework_words = {"entity", "entities", "the", "this", "that", "name", "type", "data", "item", "value", "index", "model"}
        entity_patterns -= framework_words
        if entity_patterns:
            parts.append(f"Entity types in code: {', '.join(sorted(entity_patterns)[:15])}")

        fn_patterns = set()
        for m in re.finditer(r'/functions/([a-zA-Z0-9_-]{3,40})', js):
            fn_patterns.add(m.group(1))
        if fn_patterns:
            parts.append(f"Backend functions in code: {', '.join(sorted(fn_patterns)[:15])}")
            payment_fns = [f for f in fn_patterns if any(k in f.lower() for k in ("pay", "checkout", "stripe", "invoice", "order", "subscribe"))]
            if payment_fns:
                parts.append(f"Payment-related functions: {', '.join(payment_fns)}")

        api_paths = set()
        for m in re.finditer(r'["\'](/api/[a-zA-Z0-9_/-]{5,60})["\']', js):
            path = m.group(1)
            if not any(skip in path for skip in ("/api/frontend", "/api/billing")):
                api_paths.add(path)
        if api_paths:
            parts.append(f"API endpoints: {', '.join(sorted(api_paths)[:15])}")

        return "\n".join(parts) if parts else ""
    except Exception:
        return ""


def _scan_js_security_signals(url: str, timeout: int = 3) -> dict:
    """Deep scan JS bundles for high-risk indicators: payment bypass, crypto, miners, obfuscation."""
    signals: dict = {"payment_bypass": [], "crypto": [], "miners": [], "obfuscation": False, "high_risk_libs": []}
    try:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
        all_src_lower = " ".join(scripts).lower()

        if "stripe.com" in all_src_lower or "js.stripe.com" in all_src_lower:
            signals["payment_bypass"].append("Stripe.js loaded directly")
        if "paypal.com/sdk" in all_src_lower:
            signals["payment_bypass"].append("PayPal SDK loaded directly")
        if "checkout.razorpay.com" in all_src_lower:
            signals["payment_bypass"].append("Razorpay checkout loaded")

        inline_scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
        inline_text = " ".join(inline_scripts).lower()

        for miner in ("coinhive", "coin-hive", "crypto-loot", "cryptoloot", "jsecoin", "webminepool"):
            if miner in all_src_lower or miner in inline_text:
                signals["miners"].append(miner)

        for crypto_sig in ("web3.js", "ethers.js", "web3modal", "@solana/web3", "metamask", "walletconnect"):
            if crypto_sig in all_src_lower or crypto_sig in inline_text:
                signals["crypto"].append(crypto_sig)

        skip_names = ("framework.", "main.", "badge.", "google", "tailwind", "gsi/client")
        app_chunks = [s if s.startswith("http") else f"{origin}{s}"
                      for s in scripts if not any(skip in s.lower() for skip in skip_names)]

        for chunk_url in app_chunks[:2]:
            try:
                req2 = Request(chunk_url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req2, timeout=timeout) as resp2:
                    js = resp2.read().decode("utf-8", errors="replace")

                js_lower = js.lower()
                for kw in ("stripe", "paymentintent", "payment_intent", "checkout.session"):
                    if kw in js_lower:
                        signals["payment_bypass"].append(f"'{kw}' found in app JS bundle")
                        break
                for kw in ("ethereum", "solana", "blockchain", "nft", "web3", "metamask", "wallet"):
                    if kw in js_lower:
                        signals["crypto"].append(f"'{kw}' reference in JS")
                        break
                if len(re.findall(r'\\x[0-9a-f]{2}', js[:5000])) > 50:
                    signals["obfuscation"] = True
            except Exception:
                pass

        signals["payment_bypass"] = list(set(signals["payment_bypass"]))
        signals["crypto"] = list(set(signals["crypto"]))
        signals["miners"] = list(set(signals["miners"]))
    except Exception:
        pass
    return signals


def _probe_legal_pages(url: str, timeout: int = 3) -> dict:
    """Probe for /privacy-policy, /terms, /legal pages. Return presence and key signals."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    legal_paths = ["/privacy-policy", "/privacy", "/terms", "/terms-of-service", "/tos", "/legal", "/about"]
    results: dict = {"pages_found": [], "pages_missing": [], "has_address": False, "has_company_name": False, "has_contact": False}

    for path in legal_paths:
        try:
            full_url = f"{origin}{path}"
            req = Request(full_url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
            with urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    body = resp.read().decode("utf-8", errors="replace")[:5000].lower()
                    if len(body) > 200:
                        results["pages_found"].append(path)
                        if re.search(r'\d{3,5}\s+[a-z]', body) or "street" in body or "avenue" in body or "suite" in body:
                            results["has_address"] = True
                        if any(w in body for w in ("inc.", "llc", "ltd", "gmbh", "corp", "company", "registered")):
                            results["has_company_name"] = True
                        if any(w in body for w in ("@", "email", "contact us", "support@", "phone")):
                            results["has_contact"] = True
                    continue
        except Exception:
            pass
        results["pages_missing"].append(path)

    return results


def _capture_screenshot(url: str, save_dir: Optional[Path] = None) -> Optional[str]:
    """Take a full-page screenshot via Playwright. Returns saved file path or None."""
    browser = _get_or_create_browser()
    if browser is None:
        return None
    save_dir = save_dir or (PROJECT_ROOT / "output" / "screenshots")
    save_dir.mkdir(parents=True, exist_ok=True)

    parsed = urlparse(url)
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', parsed.netloc + parsed.path)[:60]
    filepath = save_dir / f"{safe_name}_{int(time.time())}.png"

    try:
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(url, wait_until="domcontentloaded", timeout=8000)
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        for _ in range(6):
            ready = page.evaluate("""() => {
                const root = document.getElementById('root') || document.getElementById('app');
                if (root && root.children.length > 0) {
                    return (document.body.innerText || '').trim().length > 30;
                }
                return false;
            }""")
            if ready:
                break
            page.wait_for_timeout(300)
        page.screenshot(path=str(filepath), full_page=True)
        context.close()
        return str(filepath)
    except Exception:
        try:
            context.close()
        except Exception:
            pass
        return None


def _flatten_json_for_text(obj, max_chars: int = 2000) -> str:
    """Convert JSON to readable text for API response capture."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj[:max_chars]
    if isinstance(obj, (int, float, bool)):
        return str(obj)
    if isinstance(obj, list):
        parts = []
        for i, item in enumerate(obj[:20]):  # limit items
            p = _flatten_json_for_text(item, max_chars // 5)
            if p:
                parts.append(p)
        return " | ".join(parts)[:max_chars]
    if isinstance(obj, dict):
        parts = []
        for k, v in obj.items():
            if k.lower() in ("name", "title", "description", "price", "product", "items", "data", "content"):
                p = _flatten_json_for_text(v, 500)
                if p:
                    parts.append(f"{k}: {p}")
        return " | ".join(parts)[:max_chars] if parts else json.dumps(obj)[:max_chars]
    return str(obj)[:max_chars]


def _extract_links_from_html(html: str, base_url: str, max_links: int = 50) -> list[str]:
    """Extract href links from HTML, resolved to absolute URLs."""
    from urllib.parse import urljoin, urlparse
    links: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = m.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if full not in seen and parsed.scheme in ("http", "https"):
            seen.add(full)
            links.append(full)
            if len(links) >= max_links:
                break
    return links


def _extract_products_from_html(html: str) -> List[dict]:
    """Extract products/items from JSON-LD and DOM (deep assets check)."""
    products: List[dict] = []
    seen_names: set = set()

    def _add(p: dict) -> None:
        name = (p.get("name") or p.get("title") or "").strip()
        if name and name not in seen_names:
            seen_names.add(name)
            products.append({
                "name": name[:200],
                "description": (p.get("description") or "").strip()[:500],
                "price": str(p.get("price") or "").strip()[:50],
            })

    # JSON-LD Product
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(m.group(1).strip())
            for item in (data if isinstance(data, list) else [data]):
                if not isinstance(item, dict):
                    continue
                t = (item.get("@type") or "").lower()
                if "product" in t:
                    offers = item.get("offers")
                    price = offers.get("price") if isinstance(offers, dict) else item.get("price") or ""
                    _add({"name": item.get("name") or item.get("title"), "description": item.get("description"), "price": price})
                for elem in item.get("itemListElement", item.get("itemlistelement", [])):
                    if isinstance(elem, dict):
                        t2 = (elem.get("@type") or "").lower()
                        if "product" in t2:
                            offers = elem.get("offers")
                            price = offers.get("price") if isinstance(offers, dict) else elem.get("price") or ""
                            _add({"name": elem.get("name") or elem.get("title"), "description": elem.get("description"), "price": price})
        except (json.JSONDecodeError, TypeError):
            pass

    # DOM (optional: BeautifulSoup)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(attrs={"data-product": True}):
            _add({
                "name": el.get("data-product-name") or el.get("data-name"),
                "description": el.get("data-product-description") or el.get("data-description"),
                "price": el.get("data-product-price") or el.get("data-price"),
            })
        for el in soup.find_all(class_=re.compile(r"product|item|card")):
            name_el = el.find(class_=re.compile(r"name|title|product-name"))
            desc_el = el.find(class_=re.compile(r"description|desc"))
            price_el = el.find(class_=re.compile(r"price|cost"))
            name = (name_el.get_text() if name_el else "").strip()[:200]
            desc = (desc_el.get_text() if desc_el else "").strip()[:500]
            price = (price_el.get_text() if price_el else "").strip()[:50]
            if name or desc:
                _add({"name": name, "description": desc, "price": price})
    except ImportError:
        pass

    return products[:20]


def _extract_text_from_html(html: str, base_url: str = "", max_chars: int = 15000) -> str:
    """Fallback: extract text from meta tags, JSON-LD, and links when innerText is sparse."""
    if not html or len(html) < 50:
        return ""
    parts: list[str] = []
    # Meta description, og:title, og:description
    for pattern, group in [
        (r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', 1),
        (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']', 1),
        (r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', 1),
        (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', 1),
        (r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', 1),
        (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', 1),
    ]:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            parts.append(m.group(group).strip())
    # JSON-LD: name, description from Product/Organization
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(m.group(1).strip())
            for item in (data if isinstance(data, list) else [data]):
                if isinstance(item, dict):
                    for k in ("name", "description", "title"):
                        v = item.get(k)
                        if v and isinstance(v, str) and len(v) > 3:
                            parts.append(v.strip())
        except (json.JSONDecodeError, TypeError):
            pass
    # Links (available in Inspect even when visible text is minimal)
    if base_url:
        links = _extract_links_from_html(html, base_url, max_links=30)
        if links:
            parts.append("Links: " + "; ".join(links[:20]))
    return " ".join(parts)[:max_chars] if parts else ""


_pw_instance = None
_pw_browser = None
_pw_lock = threading.Lock()

def _get_or_create_browser():
    """Singleton Playwright browser — launch once, reuse across calls."""
    global _pw_instance, _pw_browser
    with _pw_lock:
        if _pw_browser is not None:
            try:
                _pw_browser.contexts  # check if still alive
                return _pw_browser
            except Exception:
                _pw_browser = None
                _pw_instance = None
        try:
            from playwright.sync_api import sync_playwright
            _pw_instance = sync_playwright().start()
            _pw_browser = _pw_instance.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-extensions", "--disable-background-networking"],
            )
            return _pw_browser
        except Exception:
            return None

def _scrape_with_playwright(url: str, timeout_ms: int = 12000, max_chars: int = 15000, deep: bool = False) -> Optional[str]:
    """Use Playwright to render JavaScript, capture API responses, extract text. Optionally follow internal links."""
    browser = _get_or_create_browser()
    if browser is None:
        return None
    hard_deadline = time.time() + (timeout_ms / 1000) + 3
    api_responses: List[dict] = []
    entity_responses: List[dict] = []
    function_calls_seen: List[str] = []
    is_base44_detected = [False]
    static_suffixes = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".svg", ".map")

    skip_domains = ("stripe.com", "google-analytics", "googletagmanager", "facebook.com/tr", "doubleclick.net")
    block_domains = (
        "google-analytics.com", "googletagmanager.com", "facebook.net", "facebook.com/tr",
        "doubleclick.net", "hotjar.com", "segment.io", "segment.com", "mixpanel.com",
        "clarity.ms", "sentry.io", "intercom.io", "crisp.chat", "tawk.to",
        "cdn.onesignal.com", "ads.google.com", "adservice.google.com",
    )

    def _on_response(response):
        try:
            if response.status >= 400:
                return
            resp_url = response.url
            resp_lower = resp_url.lower()
            if any(resp_lower.endswith(s) for s in static_suffixes):
                return
            if any(skip in resp_lower for skip in skip_domains):
                return

            if "base44" in resp_lower or "/api/apps/" in resp_lower:
                is_base44_detected[0] = True

            if "/functions/" in resp_lower and len(function_calls_seen) < 20:
                fn_m = re.search(r"/functions/([a-zA-Z0-9_-]+)", resp_url)
                if fn_m:
                    function_calls_seen.append(fn_m.group(1))

            ct = (response.headers.get("content-type") or "").lower()
            if "application/json" not in ct and "text/json" not in ct:
                return
            body = response.json()
            if not body:
                return

            is_entity = "/api/entities/" in resp_lower or "/api/apps/" in resp_lower
            if is_entity and isinstance(body, (list, dict)):
                if isinstance(body, list) and len(body) > 0 and isinstance(body[0], dict):
                    entity_name_m = re.search(r"/entities/([a-zA-Z0-9_-]+)", resp_url)
                    entity_name = entity_name_m.group(1) if entity_name_m else "unknown"
                    if len(entity_responses) < 15:
                        entity_responses.append({
                            "entity": entity_name,
                            "url": resp_url[:200],
                            "count": len(body),
                            "sample_keys": list(body[0].keys())[:15] if body else [],
                            "data": body[:5],
                        })
                elif isinstance(body, dict) and len(entity_responses) < 15:
                    entity_name_m = re.search(r"/entities/([a-zA-Z0-9_-]+)", resp_url)
                    entity_name = entity_name_m.group(1) if entity_name_m else "unknown"
                    entity_responses.append({
                        "entity": entity_name,
                        "url": resp_url[:200],
                        "count": 1,
                        "sample_keys": list(body.keys())[:15],
                        "data": body,
                    })

            if len(api_responses) < 15:
                api_responses.append({"url": resp_url[:200], "data": body})
        except Exception:
            pass

    def _block_route(route):
        """Abort requests to analytics/ads domains to speed up page load."""
        try:
            route.abort()
        except Exception:
            pass

    try:
        context = None
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.on("response", _on_response)
            for bd in block_domains:
                try:
                    page.route(f"**/*{bd}*", _block_route)
                except Exception:
                    pass
            page.set_default_timeout(min(timeout_ms, 12000))
            page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_ms, 8000))
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass

            # Smart wait: poll DOM until React root has children (max 2s, 200ms intervals)
            for _ in range(10):
                ready = page.evaluate("""() => {
                    const root = document.getElementById('root') || document.getElementById('app');
                    if (root && root.children.length > 0) {
                        const text = (document.body.innerText || '').trim();
                        return text.length > 30 && !text.toLowerCase().startsWith('enable javascript');
                    }
                    return false;
                }""")
                if ready:
                    break
                page.wait_for_timeout(200)

            try:
                page.evaluate("""() => { window.scrollTo(0, document.body.scrollHeight); }""")
            except Exception:
                pass

            text = page.evaluate("""() => {
                const body = document.body;
                if (!body) return '';
                return body.innerText || body.textContent || '';
            }""")
            if text and isinstance(text, str):
                text = re.sub(r"\s+", " ", text).strip()

            out = ""
            if text and isinstance(text, str):
                text = re.sub(r"\s+", " ", text).strip()
                tl = text.lower()
                auth_phrases = ("sign in to continue", "log in to continue", "login required",
                                "access denied", "please sign in", "authentication required",
                                "sign up to continue", "create an account", "register to continue")
                base44_auth_markers = ("base44.com/auth", "base44.app/auth", "/api/apps/auth/")
                current_url = ""
                try:
                    current_url = page.url
                except Exception:
                    pass
                is_auth_redirect = any(m in current_url.lower() for m in base44_auth_markers)
                has_auth_text = any(p in tl for p in auth_phrases) and len(text) < 500

                if is_auth_redirect or has_auth_text:
                    if is_auth_redirect:
                        out = "[Private app — login required (redirected to Base44 auth)] " + text[:300]
                    elif "base44" in tl or is_base44_detected[0]:
                        out = "[Private Base44 app — login required] " + text[:300]
                    else:
                        out = "[Auth wall detected] " + text[:300]
                else:
                    out = (text[:max_chars] + "...") if len(text) > max_chars else text
                if "waiting to shine" in out.lower() or len(out) < 150:
                    try:
                        links_data = page.evaluate("""() => {
                            const links = [];
                            document.querySelectorAll('a[href]').forEach(a => {
                                const h = (a.getAttribute('href') || '').trim();
                                if (h && !h.startsWith('#') && !h.startsWith('javascript:')) {
                                    try { links.push(new URL(h, window.location.href).href); } catch (_) {}
                                }
                            });
                            return [...new Set(links)].slice(0, 30);
                        }""")
                        if links_data and isinstance(links_data, list) and len(links_data) > 0:
                            out = out + "\n\nLinks: " + "; ".join(str(l) for l in links_data[:20])
                    except Exception:
                        pass

            if not out or len(out) < 100 or "enable javascript" in (out or "").lower() or "waiting to shine" in (out or "").lower():
                html = page.content()
                fallback = _extract_text_from_html(html, url, max_chars)
                if fallback:
                    out = fallback
                elif not out:
                    try:
                        links_data = page.evaluate("""() => {
                            const links = [];
                            document.querySelectorAll('a[href]').forEach(a => {
                                const h = (a.getAttribute('href') || '').trim();
                                if (h && !h.startsWith('#') && !h.startsWith('javascript:')) {
                                    try { links.push(new URL(h, window.location.href).href); } catch (_) {}
                                }
                            });
                            return [...new Set(links)].slice(0, 30);
                        }""")
                        if links_data and isinstance(links_data, list) and len(links_data) > 0:
                            out = "Links: " + "; ".join(str(l) for l in links_data[:20])
                    except Exception:
                        pass

            if not out and text:
                out = re.sub(r"\s+", " ", (text or "").strip())
                if out:
                    out = (out[:max_chars] + "...") if len(out) > max_chars else out

            try:
                html_for_products = page.content()
                products = _extract_products_from_html(html_for_products)
                if products:
                    items_text = "\n".join(
                        f"- {p['name']}" + (f" — {p['price']}" if p.get("price") else "") + (f": {p['description'][:100]}…" if p.get("description") else "")
                        for p in products[:15]
                    )
                    out = (out or "") + "\n\n[Items sold (extracted)]\n" + items_text
            except Exception:
                pass

            if deep and out and time.time() < hard_deadline - 2:
                try:
                    links_data = page.evaluate("""() => {
                        const base = window.location.origin;
                        const links = [];
                        document.querySelectorAll('a[href]').forEach(a => {
                            const h = (a.getAttribute('href') || '').trim();
                            if (!h || h.startsWith('#') || h.startsWith('javascript:')) return;
                            try {
                                const u = new URL(h, window.location.href);
                                if (u.origin === base && u.href !== window.location.href) links.push(u.href);
                            } catch (_) {}
                        });
                        return [...new Set(links)].slice(0, 10);
                    }""")
                    if links_data and isinstance(links_data, list):
                        base_parsed = urlparse(url)
                        base_domain = base_parsed.netloc
                        visited = {url}
                        extra_pages: List[str] = []
                        for link in links_data[:2]:
                            if time.time() > hard_deadline - 1:
                                break
                            if link in visited:
                                continue
                            link_parsed = urlparse(link)
                            if link_parsed.netloc != base_domain:
                                continue
                            visited.add(link)
                            try:
                                page.goto(link, wait_until="domcontentloaded", timeout=3000)
                                page.wait_for_timeout(300)
                                pt = page.evaluate("""() => {
                                    const b = document.body;
                                    return b ? (b.innerText || b.textContent || '').trim() : '';
                                }""")
                                if pt and isinstance(pt, str) and len(pt) > 20:
                                    pt = re.sub(r"\s+", " ", pt)[:2000]
                                    extra_pages.append(f"\n\n--- Page: {link} ---\n{pt}")
                            except Exception:
                                pass
                        if extra_pages:
                            out = out + "\n\n[Deep scrape - linked pages]" + "".join(extra_pages)
                except Exception:
                    pass

            if entity_responses:
                entity_parts = []
                for er in entity_responses[:10]:
                    ename = er.get("entity", "?")
                    ecount = er.get("count", 0)
                    ekeys = er.get("sample_keys", [])
                    line = f"Entity '{ename}': {ecount} records, fields: {', '.join(str(k) for k in ekeys[:12])}"
                    sample_data = er.get("data")
                    if sample_data:
                        flat = _flatten_json_for_text(sample_data, 2000)
                        if flat and len(flat) > 10:
                            line += f"\n  Sample: {flat[:2000]}"
                    entity_parts.append(line)
                if entity_parts:
                    out = (out or "") + "\n\n[Entity data captured (data model)]\n" + "\n".join(entity_parts)

            if function_calls_seen:
                unique_fns = list(dict.fromkeys(function_calls_seen))
                payment_fns = [f for f in unique_fns if any(k in f.lower() for k in ("checkout", "payment", "stripe", "pay", "subscribe", "invoice", "refund", "order"))]
                fn_text = "Functions called: " + ", ".join(unique_fns[:20])
                if payment_fns:
                    fn_text += f"\n  [Payment-related functions detected]: {', '.join(payment_fns)}"
                out = (out or "") + "\n\n[Backend functions detected]\n" + fn_text

            if api_responses:
                api_text_parts = []
                for r in api_responses[:8]:
                    flat = _flatten_json_for_text(r.get("data"), 2000)
                    if flat:
                        api_text_parts.append(f"[{r.get('url', '')[:80]}]: {flat}")
                if api_text_parts:
                    out = (out or "") + "\n\n[API responses captured]\n" + "\n".join(api_text_parts)

            if not ("base44.app" in url) and is_base44_detected[0]:
                try:
                    page_html = page.content()
                    if _detect_base44_from_html(page_html):
                        out = (out or "") + "\n\n[Base44 platform detected on custom domain]"
                except Exception:
                    pass

            if out:
                return (out[:max_chars * 2] + "\n\n[... truncated ...]") if len(out) > max_chars * 2 else out
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _fetch_base44_public_apis(base_url: str, timeout: int = 8, force: bool = False) -> str:
    """
    Base44-specific: probe known public API endpoints. No auth required.
    Returns appended text with app_id, app_name, config, visibility, payments, auth methods, etc.
    force=True skips the base44.app domain check (for custom-domain Base44 apps).
    """
    if not force and "base44.app" not in base_url and "base44.com" not in base_url:
        return ""
    try:
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
        domain = parsed.netloc
        parts: List[str] = []

        # Extract app_id directly from URL path (/app/<id>) if present
        app_id_from_path = ""
        path_match = re.search(r"/app/([a-z0-9]{20,})", parsed.path)
        if path_match:
            app_id_from_path = path_match.group(1)

        app_id_raw = app_id_from_path
        if not app_id_raw:
            domain_url = f"{origin}/api/apps/public/prod/domain/{domain}"
            try:
                req = Request(domain_url, headers={"User-Agent": "Mozilla/5.0 (compatible; Base44-UW/1.0)"})
                with urlopen(req, timeout=timeout) as r:
                    raw = r.read().decode("utf-8", errors="replace").strip()
                    app_id_raw = raw.strip('"')
                    try:
                        parsed_json = json.loads(raw)
                        app_id_raw = str(parsed_json) if isinstance(parsed_json, (str, int)) else (parsed_json.get("app_id") or parsed_json.get("appId") or "")
                    except (json.JSONDecodeError, TypeError):
                        pass
            except Exception:
                pass

        if app_id_raw and len(app_id_raw) >= 20 and app_id_raw.replace("-", "").replace("_", "").isalnum():
            parts.append(f"App ID (from Base44 API): {app_id_raw}")
            login_info_ok = False

            login_url = f"{origin}/api/apps/public/login-info/by-id/{app_id_raw}"
            try:
                req2 = Request(login_url, headers={"User-Agent": "Mozilla/5.0 (compatible; Base44-UW/1.0)"})
                with urlopen(req2, timeout=min(timeout, 2)) as r2:
                    data = json.loads(r2.read().decode("utf-8", errors="replace"))
                    login_info_ok = True
                    if isinstance(data, dict):
                        name = data.get("name") or data.get("appName") or data.get("title")
                        if name:
                            parts.append(f"App name (from Base44 API): {name}")
                        desc = data.get("description") or ""
                        if desc and isinstance(desc, str) and len(desc.strip()) > 3:
                            parts.append(f"App description: {desc.strip()[:500]}")

                        visibility = data.get("visibility") or data.get("appVisibility") or data.get("access")
                        if visibility:
                            parts.append(f"Visibility: {visibility}")
                        login_req = data.get("requireLogin") or data.get("loginRequired") or data.get("require_login")
                        if login_req is not None:
                            parts.append(f"Login required: {login_req}")

                        login_methods = data.get("loginMethods") or data.get("authMethods")
                        if login_methods is not None:
                            parts.append(f"Login methods: {json.dumps(login_methods)[:300]}")

                        for k in ("publicSettings", "settings", "features", "enabledFeatures"):
                            v = data.get(k)
                            if v is not None:
                                parts.append(f"{k}: {json.dumps(v)[:300]}")

                        payment_keys = ("payments", "paymentProvider", "stripeEnabled", "wixPayments",
                                        "hasPayments", "paymentMethods", "stripe", "payment")
                        for pk in payment_keys:
                            pv = data.get(pk)
                            if pv is not None:
                                parts.append(f"[Payment signal] {pk}: {json.dumps(pv)[:200]}")

                        integration_keys = ("integrations", "connectors", "enabledIntegrations")
                        for ik in integration_keys:
                            iv = data.get(ik)
                            if iv is not None:
                                parts.append(f"Integrations ({ik}): {json.dumps(iv)[:300]}")

                        agents_val = data.get("agents") or data.get("aiAgents")
                        if agents_val is not None:
                            parts.append(f"AI Agents: {json.dumps(agents_val)[:200]}")

                        remaining_keys = set(data.keys()) - {
                            "name", "appName", "title", "description", "visibility",
                            "appVisibility", "access", "requireLogin", "loginRequired",
                            "require_login", "loginMethods", "authMethods", "publicSettings",
                            "settings", "features", "enabledFeatures", "payments",
                            "paymentProvider", "stripeEnabled", "wixPayments", "hasPayments",
                            "paymentMethods", "stripe", "payment", "integrations",
                            "connectors", "enabledIntegrations", "agents", "aiAgents",
                        }
                        for rk in sorted(remaining_keys):
                            rv = data.get(rk)
                            if rv is not None and rv != "" and rv != [] and rv != {}:
                                flat = _flatten_json_for_text(rv, 200)
                                if flat and len(flat) > 2:
                                    parts.append(f"{rk}: {flat}")
            except Exception:
                pass

            for path in ["/api/apps/public/config", "/api/apps/public/app-info"] if login_info_ok else []:
                try:
                    cfg_url = f"{origin}{path}"
                    req3 = Request(cfg_url, headers={"User-Agent": "Mozilla/5.0 (compatible; Base44-UW/1.0)"})
                    with urlopen(req3, timeout=1) as r3:
                        cfg = r3.read().decode("utf-8", errors="replace")
                        if cfg and len(cfg) > 2 and cfg.strip().startswith("{"):
                            cdata = json.loads(cfg)
                            flat = _flatten_json_for_text(cdata, 800)
                            if flat:
                                parts.append(f"Config ({path}): {flat}")
                except Exception:
                    pass

        if parts:
            return "\n\n[Base44 public API — no auth]\n" + "\n".join(parts)
    except Exception:
        pass
    return ""


def _parse_base44_api_from_scraped(scraped: str) -> tuple[Optional[str], Optional[str]]:
    """Extract App ID and App name from [Base44 public API — no auth] section. Returns (app_id, app_name)."""
    if not scraped or "Base44 public API" not in scraped:
        return (None, None)
    app_id, app_name = None, None
    m = re.search(r"App ID \(from Base44 API\):\s*([a-f0-9]{24})", scraped)
    if m:
        app_id = m.group(1)
    m = re.search(r"App name \(from Base44 API\):\s*(.+?)(?:\n|$)", scraped)
    if m:
        app_name = m.group(1).strip()
    return (app_id, app_name)


def _format_error_if_json(content: str) -> str:
    """If content is an error JSON (e.g. App not found), return a cleaner message."""
    if not content or len(content) > 2000:
        return content
    stripped = content.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return content
    try:
        data = json.loads(stripped)
        if not isinstance(data, dict):
            return content
        msg = data.get("message") or data.get("detail") or data.get("error") or data.get("error_type")
        if not msg:
            return content
        err_type = data.get("error_type", "")
        if err_type:
            return f"Server error: {msg} ({err_type})"
        return f"Server error: {msg}"
    except (json.JSONDecodeError, TypeError):
        return content


def _urllib_fetch(url: str, timeout: int = 8, max_chars: int = 15000) -> tuple[Optional[str], Optional[str]]:
    """Fast HTTP fetch with HTML→text. Returns (clean_text, raw_html) or (None, None).
    raw_html is returned for meta tag extraction even when text extraction fails."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        with urlopen(req, timeout=timeout) as resp:
            raw_html = resp.read().decode("utf-8", errors="replace")
        cleaned = re.sub(r"<script[^>]*>.*?</script>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<style[^>]*>.*?</style>", " ", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and len(cleaned) > 200 and "enable javascript" not in cleaned.lower() and "waiting to shine" not in cleaned.lower():
            text = (cleaned[:max_chars] + "...") if len(cleaned) > max_chars else cleaned
            return (text, raw_html)
        return (None, raw_html)
    except Exception:
        pass
    return (None, None)


def scrape_app_url(url: str, timeout_seconds: int = 15, max_chars: int = 15000, deep: bool = False) -> Optional[str]:
    """Fetch URL and return plain text. Architecture-aware layered strategy.
    Base44 apps: API + meta tags (~2s). Playwright only when deep=True.
    Other URLs: urllib (~1s) → Playwright only if urllib fails.
    deep=True: also launch Playwright for JS rendering + link following."""
    if not url or not url.startswith("http"):
        return None

    is_base44 = "base44.app" in url or "base44.com" in url
    base44_api = ""
    meta_tags = ""
    sitemap_text = ""
    frontend_config = ""
    js_signals = ""

    def _assemble(main_content: Optional[str] = None) -> Optional[str]:
        parts = []
        if meta_tags:
            parts.append(f"[SEO meta tags] {meta_tags}")
        if main_content:
            parts.append(main_content)
        if base44_api:
            parts.append(base44_api)
        if frontend_config:
            parts.append(f"\n[Platform config]\n{frontend_config}")
        if js_signals:
            parts.append(f"\n[JS bundle analysis]\n{js_signals}")
        if sitemap_text:
            parts.append(sitemap_text)
        return _format_error_if_json("\n\n".join(parts)) if parts else None

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    def _run_playwright_with_timeout(pw_timeout_s: int) -> Optional[str]:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_scrape_with_playwright, url, pw_timeout_s * 1000, max_chars, deep)
            try:
                return fut.result(timeout=pw_timeout_s + 3)
            except (FuturesTimeout, Exception):
                return None

    if is_base44:
        with ThreadPoolExecutor(max_workers=5) as pool:
            api_future = pool.submit(_fetch_base44_public_apis, url, 2)
            def _get_meta():
                try:
                    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
                    with urlopen(req, timeout=2) as resp:
                        return _extract_meta_tags(resp.read().decode("utf-8", errors="replace"))
                except Exception:
                    return ""
            meta_future = pool.submit(_get_meta)
            sitemap_future = pool.submit(_fetch_sitemap, url, 2)
            config_future = pool.submit(_fetch_frontend_config, url, 2)
            js_future = pool.submit(_scan_js_bundle_for_signals, url, 3) if deep else None
            base44_api = api_future.result() or ""
            meta_tags = meta_future.result() or ""
            sitemap_pages = sitemap_future.result() or []
            frontend_config = config_future.result() or ""
            js_signals = js_future.result() or "" if js_future else ""
        if sitemap_pages:
            sitemap_text = "\n\n[Sitemap pages]\n" + "\n".join(f"- {p}" for p in sitemap_pages[:30])

        fast_result = _assemble()
        if not deep:
            return fast_result

        # Deep mode only: launch Playwright for JS rendering
        pw_out = _run_playwright_with_timeout(min(timeout_seconds, 10))
        return _assemble(pw_out) or fast_result

    # Non-Base44: fast urllib + sitemap in parallel
    fast_text, raw_html = None, None
    sitemap_pages = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        urllib_future = pool.submit(_urllib_fetch, url, min(timeout_seconds, 6), max_chars)
        sitemap_future = pool.submit(_fetch_sitemap, url, 3)
        fast_text, raw_html = urllib_future.result()
        sitemap_pages = sitemap_future.result() or []
    if sitemap_pages:
        sitemap_text = "\n\n[Sitemap pages]\n" + "\n".join(f"- {p}" for p in sitemap_pages[:30])
    if raw_html:
        meta_tags = _extract_meta_tags(raw_html)
        if _detect_base44_from_html(raw_html):
            base44_api = _fetch_base44_public_apis(url, timeout=4, force=True)

    if fast_text:
        return _assemble(fast_text)

    # Playwright only if urllib failed
    pw_out = _run_playwright_with_timeout(min(timeout_seconds, 10))
    if pw_out and "enable javascript" not in (pw_out or "").lower():
        if not base44_api and "Base44 platform detected on custom domain" in (pw_out or ""):
            base44_api = _fetch_base44_public_apis(url, timeout=4, force=True)
        return _assemble(pw_out)

    fallback_text, _ = _urllib_fetch(url, timeout=timeout_seconds, max_chars=max_chars)
    if fallback_text:
        return _assemble(fallback_text)

    return _assemble(pw_out) or _assemble()


def build_middleman_prompt(
    app_name: str,
    app_url: Optional[str],
    conversation_summary: str,
    scraped_content: Optional[str],
    user_description: Optional[str] = None,
) -> str:
    body = _extract_prompt_body(MIDDLEMAN_TEMPLATE)
    scraped = scraped_content if scraped_content else "Not available (app not scraped or not public)."
    if app_url and not scraped_content:
        scraped = f"App URL: {app_url}. Content: Not available (not scraped)."
    ud = (user_description or "").strip() or "Not provided."
    return body.replace("[app_name]", app_name or "Unknown").replace(
        "[app_url]", app_url or "N/A"
    ).replace("[user_description]", ud).replace(
        "[conversation_summary]", conversation_summary or "(No conversation summary available)"
    ).replace("[scraped_content]", scraped)


def build_policy_prompt(app_summary: str, policy_excerpt: str) -> str:
    body = _extract_prompt_body(POLICY_COMPARISON_TEMPLATE)
    return body.replace("[app_summary]", app_summary).replace("[policy_excerpt]", policy_excerpt)


def build_prompt(policy_excerpt: str, conversation_summary: str, app_url: Optional[str] = None, scraped_content: Optional[str] = None) -> str:
    body = _get_prompt_body()
    evidence2 = scraped_content if scraped_content else "Not available (app not scraped or not public)"
    if app_url and not scraped_content:
        evidence2 = f"App URL: {app_url}. Content: Not available (not scraped)."
    return body.replace(
        "[Paste or reference the relevant policy sections / criteria from the policy doc here]",
        policy_excerpt
    ).replace(
        "[Paste the conversation summary text]",
        conversation_summary or "(No conversation summary available)"
    ).replace(
        "[Paste or summarize the scraped content, e.g. landing page, key disclosures; or write \"Not available\" or \"App not public\"]",
        evidence2
    )


def _call_openai(prompt: str, model: str, delay_after_seconds: float) -> Optional[str]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    try:
        client = OpenAI(api_key=key)
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        if delay_after_seconds > 0:
            time.sleep(delay_after_seconds)
        return r.choices[0].message.content.strip()
    except Exception:
        return None


def _call_ollama(prompt: str, model: str, delay_after_seconds: float, timeout_seconds: int = 20) -> Optional[str]:
    """Call local Ollama (no API key). Requires Ollama running with the given model."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode("utf-8")
    for attempt in range(2):
        try:
            req = Request(
                "http://127.0.0.1:11434/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=timeout_seconds) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            if delay_after_seconds > 0:
                time.sleep(delay_after_seconds)
            msg = out.get("message") or {}
            content = (msg.get("content") or "").strip()
            if content and len(content) > 100:
                return content
        except Exception:
            if attempt < 1:
                time.sleep(2)
            continue
    return None


def _extract_product_cues(scraped: str, conversation: str) -> list[str]:
    """Rule-based extraction: find potential product/service/price cues. Prefer scraped content."""
    text = (scraped or "").strip()
    if not text or len(text) < 15:
        text = (conversation or "").strip()
    if not text or len(text) < 15:
        return []
    text_lower = text.lower()
    cues: list[str] = []
    seen: set[str] = set()

    def _add(c: str) -> None:
        c = c.strip()
        if not c or len(c) > 120:
            return
        # Skip placeholders and junk
        if c in ("(no scraped content)", "(no conversation log)"):
            return
        if c in seen:
            return
        # Skip if substring of existing or vice versa
        if any(c in s or s in c for s in seen):
            return
        # Skip if highly overlapping by words (avoid "Basic plan $50..." vs "asic plan $50...")
        words_c = set(w.lower() for w in re.findall(r"\w+", c))
        for s in seen:
            words_s = set(w.lower() for w in re.findall(r"\w+", s))
            overlap = len(words_c & words_s) / min(len(words_c), len(words_s)) if words_c and words_s else 0
            if overlap >= 0.65:
                return
        seen.add(c)
        cues.append(c)

    # Price patterns — capture surrounding context so "$50" becomes "Plan $50/month" etc.
    for m in re.finditer(r"[$€£]\s*[\d,]+(?:\.\d{2})?|[\d,]+(?:\.\d{2})?\s*(?:USD|EUR|GBP|dollars?|euros?|pounds?)", text, re.I):
        start = max(0, m.start() - 45)
        end = min(len(text), m.end() + 35)
        snippet = text[start:end].strip()
        if len(snippet) >= 5:
            _add(snippet)
        else:
            _add(m.group(0))

    # Product/service keywords — one snippet per keyword, ~60 chars around first occurrence (whole-word match)
    keywords = (
        "subscription", "premium", "buy", "purchase", "shop", "order", "ticket",
        "access", "membership", "plan", "pricing", "sells", "offers", "provides",
        "digital goods", "physical goods", "event", "course", "ebook",
    )
    for kw in keywords:
        match = re.search(r"\b" + re.escape(kw) + r"\b", text_lower)
        if match and len(cues) < 8:
            idx = match.start()
            kw_len = match.end() - match.start()
            start = max(0, idx - 25)
            # Prefer starting at word boundary (avoid "h. Premium" fragments)
            space_before = text.rfind(" ", 0, start) + 1
            if space_before > start - 15:
                start = space_before
            end = min(len(text), idx + kw_len + 35)
            snippet = text[start:end].strip()
            # Skip fragments that start with orphan chars (e.g. "onth.", "h.")
            if len(snippet) >= 10 and not re.match(r"^[a-z]{1,2}\.\s", snippet):
                _add(snippet)

    return cues[:8]


def _infer_intent_from_conversation(conv: str) -> str:
    """Rule-based: extract app intent from conversation (e.g. 'building X application')."""
    if not conv or len(conv) < 20:
        return ""
    conv_lower = conv.lower()
    for pattern, repl in [
        (r"building\s+(?:a\s+)?([^.]{10,80}?)(?:\.|application|app|platform)", r"\1 application"),
        (r"user\s+is\s+building\s+([^.]{10,80}?)(?:\.|$)", r"\1"),
        (r"(?:creates?|provides?|offers?)\s+([^.]{10,80}?)(?:\.|$)", r"\1"),
        (r"platform\s+for\s+([^.]{10,80}?)(?:\.|$)", r"\1"),
        (r"application\s+(?:for|to)\s+([^.]{10,80}?)(?:\.|$)", r"\1"),
    ]:
        m = re.search(pattern, conv, re.IGNORECASE | re.DOTALL)
        if m:
            s = m.group(1).strip()
            if len(s) > 15 and len(s) < 100:
                return s
    if "wardrobe" in conv_lower:
        return "Wardrobe / outfit management"
    if "event" in conv_lower and "ticket" in conv_lower:
        return "Event ticketing"
    if "subscription" in conv_lower or "membership" in conv_lower:
        return "Subscription or membership service"
    return ""


def _parse_items_from_scraped(scraped: str) -> list[str]:
    """Parse [Items sold (extracted)] and similar sections from scraped content."""
    items: list[str] = []
    m = re.search(r"\[Items sold \(extracted\)\]\s*\n(.*?)(?=\n\n\[|\n\n---|\Z)", scraped, re.DOTALL)
    if m:
        for line in m.group(1).strip().split("\n"):
            line = line.strip()
            if line.startswith("-") and len(line) > 2:
                items.append(line[1:].strip())
    return items[:15]


def _extract_fields_from_scraped(scraped: str) -> dict:
    """Parse structured fields from scraped content (Base44 API, meta tags, platform config)."""
    fields: dict = {}
    if not scraped:
        return fields
    for pattern, key in [
        (r'App name \(from Base44 API\):\s*(.+)', 'app_name'),
        (r'App description:\s*(.+)', 'app_description'),
        (r'user_description:\s*(.+)', 'user_description'),
        (r'Title:\s*([^|]+)', 'meta_title'),
        (r'(?:og:description|description):\s*([^|]+)', 'meta_description'),
        (r'Visibility:\s*(.+)', 'visibility'),
        (r'Login required:\s*(.+)', 'login_required'),
        (r'Login methods:\s*(.+)', 'login_methods'),
        (r'auth_config:\s*(.+)', 'auth_config'),
        (r'slug:\s*(.+)', 'slug'),
        (r'created_date:\s*(.+)', 'created_date'),
        (r'App ID \(from Base44 API\):\s*(.+)', 'app_id'),
    ]:
        m = re.search(pattern, scraped, re.IGNORECASE)
        if m:
            fields[key] = m.group(1).strip()
    # Entity types
    m = re.search(r'Entity types in code:\s*(.+)', scraped)
    if m:
        fields['entity_types'] = m.group(1).strip()
    # Payment signals
    payment_lines = re.findall(r'\[Payment signal\]\s*(.+)', scraped)
    if payment_lines:
        fields['payment_signals'] = "; ".join(payment_lines)
    # Integrations
    m = re.search(r'Integration:\s*(.+)', scraped)
    if m:
        fields['integrations'] = m.group(1).strip()
    # Backend functions
    m = re.search(r'Backend functions in code:\s*(.+)', scraped)
    if m:
        fields['backend_functions'] = m.group(1).strip()
    # Payment-related functions
    m = re.search(r'Payment-related functions:\s*(.+)', scraped)
    if m:
        fields['payment_functions'] = m.group(1).strip()
    return fields


def _template_app_summary(
    conversation_summary: str,
    scraped_content: Optional[str],
    user_description: Optional[str] = None,
    earliest_conversation_preview: Optional[str] = None,
) -> str:
    """No-LLM mode: rule-based inference from scraped content + conversation. No API required."""
    max_len = 50000
    scraped_raw = (scraped_content or "").strip() or "(no scraped content)"
    conv_raw = (conversation_summary or "").strip() or "(no conversation log)"
    scraped = scraped_raw[:max_len] + ("\n\n[... truncated ...]" if len(scraped_raw) > max_len else "")
    conv = conv_raw[:max_len] + ("\n\n[... truncated ...]" if len(conv_raw) > max_len else "")
    cues = _extract_product_cues(scraped_raw, conv_raw)
    items_sold = _parse_items_from_scraped(scraped_raw)

    # Parse structured fields from the scraped content
    fields = _extract_fields_from_scraped(scraped_raw)

    # --- Intent ---
    intent_parts = []
    app_name = fields.get('app_name') or fields.get('meta_title', '').strip()
    if app_name and app_name.lower() not in ('base44', ''):
        intent_parts.append(f"**{app_name}**")
    desc = fields.get('user_description') or fields.get('app_description') or fields.get('meta_description', '')
    if desc and len(desc.strip()) > 10:
        intent_parts.append(desc.strip()[:500])
    if not intent_parts:
        intent_src = (earliest_conversation_preview or "").strip() or conv_raw
        conv_intent = _infer_intent_from_conversation(intent_src)
        if conv_intent:
            intent_parts.append(conv_intent)
        elif (user_description or "").strip() and len((user_description or "").strip()) > 15:
            intent_parts.append((user_description or "").strip()[:300])
    intent_block = " — ".join(intent_parts) if intent_parts else "Not stated — review scraped content below."

    # --- Creation context ---
    creation_ctx_parts = []
    if fields.get('created_date'):
        creation_ctx_parts.append(f"Created: {fields['created_date'][:10]}")
    if fields.get('slug'):
        creation_ctx_parts.append(f"Slug: {fields['slug']}")
    if (user_description or "").strip():
        creation_ctx_parts.append("**Creator's description:** " + (user_description or "").strip()[:500])
    elif fields.get('user_description'):
        creation_ctx_parts.append("**Creator's description:** " + fields['user_description'][:500])
    if (earliest_conversation_preview or "").strip():
        ep = (earliest_conversation_preview or "").strip()[:1500]
        creation_ctx_parts.append("**Earliest conversation:**\n" + ep)
    creation_context_block = "\n".join(creation_ctx_parts) if creation_ctx_parts else "Not available."

    # --- What is sold ---
    sold_block = ""
    if items_sold:
        sold_block = "\n**Extracted items:**\n" + "\n".join(f"- {i}" for i in items_sold)
    elif cues:
        sold_block = "\n**Potential offerings (rule-based):**\n" + "\n".join(f"- {c}" for c in cues)
    if fields.get('payment_signals'):
        sold_block += f"\n**Payment signals detected:** {fields['payment_signals']}"
    if fields.get('payment_functions'):
        sold_block += f"\n**Payment functions:** {fields['payment_functions']}"
    if not sold_block.strip():
        # Infer from description keywords
        desc_lower = (desc or "").lower()
        if any(w in desc_lower for w in ("track", "manage", "monitor", "organize", "dashboard", "tool")):
            sold_block = "\nThis appears to be a **tool/utility app** (tracking, management, or dashboard). No direct product sales detected."
        elif any(w in desc_lower for w in ("shop", "store", "buy", "sell", "product", "order")):
            sold_block = "\nE-commerce signals detected — products/services likely sold. Review content for specifics."
        elif any(w in desc_lower for w in ("book", "appointment", "schedule", "reserve")):
            sold_block = "\nBooking/scheduling app detected. Services likely offered for reservation."
        else:
            sold_block = "\nNo clear paid offerings detected from available evidence."

    # --- What buyer gets ---
    buyer_gets_parts = []
    combined = (scraped_raw + " " + conv_raw + " " + (desc or "")).lower()
    if re.search(r"\bsubscription\b", combined) or re.search(r"\bmembership\b", combined):
        buyer_gets_parts.append("Access or membership")
    if re.search(r"\bticket\b", combined) or re.search(r"\bevent\b", combined):
        buyer_gets_parts.append("Event access or tickets")
    if re.search(r"\bcourse\b", combined) or re.search(r"\bebook\b", combined) or re.search(r"\btutorial\b", combined):
        buyer_gets_parts.append("Digital content (course, ebook)")
    if re.search(r"\bplan\b", combined) and ("$" in scraped_raw or "€" in scraped_raw or re.search(r"\bprice\b", combined)):
        buyer_gets_parts.append("Tiered plan access")
    if re.search(r"\btrack", combined) and re.search(r"\b(goal|commission|sales|performance)\b", combined):
        buyer_gets_parts.append("Performance tracking and visualization tools")
    if re.search(r"\bbooking\b", combined) or re.search(r"\bappointment\b", combined):
        buyer_gets_parts.append("Booking/appointment confirmation")
    if buyer_gets_parts:
        buyer_gets = "; ".join(buyer_gets_parts) + "."
    else:
        buyer_gets = "No direct purchase flow detected. App may be a free tool or require login to see offerings."

    # --- App characteristics ---
    chars_parts = []
    if fields.get('auth_config') or fields.get('login_methods'):
        auth_str = fields.get('auth_config') or fields.get('login_methods', '')
        auth_methods = []
        if re.search(r'"enable_google_login"\s*:\s*true', auth_str, re.IGNORECASE):
            auth_methods.append("Google")
        if re.search(r'"enable_username_password"\s*:\s*true', auth_str, re.IGNORECASE):
            auth_methods.append("Email/password")
        if re.search(r'"enable_microsoft_login"\s*:\s*true', auth_str, re.IGNORECASE):
            auth_methods.append("Microsoft")
        if re.search(r'"enable_facebook_login"\s*:\s*true', auth_str, re.IGNORECASE):
            auth_methods.append("Facebook")
        if re.search(r'"enable_apple_login"\s*:\s*true', auth_str, re.IGNORECASE):
            auth_methods.append("Apple")
        if auth_methods:
            chars_parts.append(f"Login methods: {', '.join(auth_methods)}")
    if fields.get('entity_types'):
        chars_parts.append(f"Data entities: {fields['entity_types']}")
    if fields.get('backend_functions'):
        chars_parts.append(f"Backend functions: {fields['backend_functions']}")
    if fields.get('integrations'):
        chars_parts.append(f"Integrations: {fields['integrations']}")
    if fields.get('visibility'):
        chars_parts.append(f"Visibility: {fields['visibility']}")
    chars_block = "\n".join(f"- {c}" for c in chars_parts) if chars_parts else ""

    word_count_scraped = len(scraped_raw.split()) if scraped_raw != "(no scraped content)" else 0
    word_count_conv = len(conv_raw.split()) if conv_raw != "(no conversation log)" else 0

    summary = f"""# App Summary (Rule-based)

**1. Intent and purpose of the app**
{intent_block}

**2. Creation context**
{creation_context_block}

**3. What is sold through the app (in detail)**
{sold_block}

**4. What the end shopper gets by buying through the app (in detail)**
{buyer_gets}"""

    if chars_block:
        summary += f"""

**5. App characteristics**
{chars_block}"""

    summary += f"""

---

**Raw evidence** ({word_count_scraped} words scraped, {word_count_conv} words conversation):

<details>
<summary>Scraped content</summary>

{scraped}
</details>

<details>
<summary>Conversation log</summary>

{conv}
</details>"""

    return summary


def _template_policy_verdict() -> str:
    """No-LLM mode: verdict MUST be Manual Review Required, not Restricted."""
    return """**Step 1 — What is sold:**  
See App Summary above.

**Step 2 — Comparison to policy:**  
Manual comparison required. Map app summary to policy categories and subcategories.

**Step 3 — Verdict:** Manual Review Required  
**Reasoning:** No LLM available (set OPENAI_API_KEY in .env or start Ollama). Review the app summary and scraped content manually.  
**Non-compliant subcategories:** Unknown."""


def _app_summary_has_no_clear_offerings(app_summary: str) -> bool:
    """True if middleman said we cannot determine what is sold (avoids policy LLM inventing products)."""
    if not app_summary or len(app_summary) < 50:
        return True
    s = app_summary.lower()
    return (
        "no clear paid offerings" in s
        or "cannot determine" in s and ("what is sold" in s or "what the end shopper" in s)
        or "not stated" in s and "what is sold" in s
        or "no information about what is being sold" in s
    )


def _template_policy_verdict_insufficient() -> str:
    """No-LLM mode: never return Restricted - Insufficient Evidence; always Manual Review Required."""
    return """**Step 1 — What is sold:**  
App summary does not state what is sold or offered for payment. Nothing is inferred.

**Step 2 — Comparison to policy:**  
Cannot compare to policy without knowing what the app sells. No categories or subcategories inferred.

**Step 3 — Verdict:** Manual Review Required  
**Reasoning:** No LLM available and insufficient evidence to determine what is sold. Set OPENAI_API_KEY in .env or start Ollama for automatic analysis.  
**Non-compliant subcategories:** Unknown."""


def get_app_summary(
    app_name: str,
    app_url: Optional[str],
    conversation_summary: str,
    scraped_content: Optional[str],
    llm_mode: str,
    model: str,
    delay: float,
    user_description: Optional[str] = None,
    earliest_conversation_preview: Optional[str] = None,
) -> str:
    """Middleman step: summarize intent, what is sold, what buyer gets."""
    prompt = build_middleman_prompt(
        app_name, app_url, conversation_summary, scraped_content, user_description=user_description
    )
    if llm_mode in ("openai", "auto"):
        out = _call_openai(prompt, model, delay)
        if out and len(out.strip()) > 100:
            return out.strip()
    if llm_mode in ("ollama", "auto"):
        ollama_model = "llama3.1:8b" if model.startswith("gpt") else model
        out = _call_ollama(prompt, ollama_model, delay)
        if out and len(out.strip()) > 100:
            return out.strip()
    return _template_app_summary(
        conversation_summary,
        scraped_content,
        user_description=user_description,
        earliest_conversation_preview=earliest_conversation_preview,
    )


def _policy_step1_invents_prohibited(app_summary: str, policy_conclusion: str) -> bool:
    """True if Step 1 in policy output lists prohibited items (e.g. firearms) not stated in app summary (hallucination)."""
    step1_m = re.search(r"\*\*Step 1[^*]*\*\*\s*(.*?)(?=\n\*\*Step 2|\Z)", policy_conclusion, re.DOTALL)
    if not step1_m:
        return False
    step1_text = (step1_m.group(1) or "").lower()
    summary_lower = (app_summary or "").lower()
    # Phrases that indicate policy prohibited categories — if Step 1 contains these but summary does not, treat as hallucination
    prohibited_phrases = (
        "functional firearms", "weapon components", "harmful ammunition",
        "weapon launchers", "disguised knives", "machetes", "nunchaku",
        "replicate firearms", "bump stocks", "suppressors", "silencers",
    )
    summary_mentions_prohibited = any(
        w in summary_lower for w in ("firearm", "weapon", "ammunition", "knife", "machete", "nunchaku")
    )
    for phrase in prohibited_phrases:
        if phrase in step1_text and not summary_mentions_prohibited:
            return True
    return False


def _policy_step2_invents_categories(policy_conclusion: str) -> bool:
    """True if Step 2 mentions policy categories (e.g. firearms, Subcategory 6) that Step 1 does not contain — treat as invalid."""
    step1_m = re.search(r"\*\*Step 1[^*]*\*\*\s*(.*?)(?=\n\*\*Step 2|\Z)", policy_conclusion, re.DOTALL)
    step2_m = re.search(r"\*\*Step 2[^*]*\*\*\s*(.*?)(?=\n\*\*Step 3|\Z)", policy_conclusion, re.DOTALL)
    if not step1_m or not step2_m:
        return False
    step1_text = (step1_m.group(1) or "").lower()
    step2_text = (step2_m.group(1) or "").lower()
    # If Step 2 mentions these but Step 1 does not, model added policy categories in Step 2
    policy_category_phrases = (
        "firearm", "ammunition", "weapon", "subcategory 6", "subcategory 1",
        "guns, knives", "adult content", "knives", "machetes", "nunchaku",
    )
    for phrase in policy_category_phrases:
        if phrase in step2_text and phrase not in step1_text:
            return True
    return False


def get_policy_conclusion(app_summary: str, policy: str, llm_mode: str, model: str, delay: float) -> str:
    """Policy step: (1) what is sold (2) compare to policy (3) Allowed/Restricted/Not-allowed + reasoning + non-compliant subcategories."""
    if _app_summary_has_no_clear_offerings(app_summary):
        return _template_policy_verdict_insufficient()
    prompt = build_policy_prompt(app_summary, policy)
    if llm_mode in ("openai", "auto"):
        out = _call_openai(prompt, model, delay)
        if out and len(out.strip()) > 80:
            if _policy_step1_invents_prohibited(app_summary, out) or _policy_step2_invents_categories(out):
                return _template_policy_verdict_insufficient()
            return out.strip()
    if llm_mode in ("ollama", "auto"):
        ollama_model = "llama3.1:8b" if model.startswith("gpt") else model
        out = _call_ollama(prompt, ollama_model, delay)
        if out and len(out.strip()) > 80:
            if _policy_step1_invents_prohibited(app_summary, out) or _policy_step2_invents_categories(out):
                return _template_policy_verdict_insufficient()
            return out.strip()
    return _template_policy_verdict()


def _template_conclusion(policy_excerpt: str, conversation_summary: str, scraped_content: Optional[str]) -> str:
    """Produce a structured conclusion without any LLM (no API key required)."""
    summary_preview = (conversation_summary or "(none)")[:2000]
    if len(conversation_summary or "") > 2000:
        summary_preview += "..."
    evidence2 = "Not available"
    if scraped_content:
        evidence2 = scraped_content[:2000] + ("..." if len(scraped_content) > 2000 else "")
    policy_ref = policy_excerpt[:1500] + ("..." if len(policy_excerpt) > 1500 else "")
    return f"""## Policy vs evidence

**Policy (excerpt):**  
{policy_ref}

**Checked:** Conversation summary; public app content (if scraped).

**Finding (conversation summary):**  
{summary_preview}

**Finding (public app content):**  
{evidence2}

---

## Reasoning with reference to policy document

Using the policy excerpt above: evidence from the conversation summary and (where available) public app content was compared to the policy criteria. The policy document defines supportability requirements, prohibited categories (e.g. restricted products, illicit activities), and disclosure obligations. No automated evaluation was performed (no LLM was used). Manual review is required: (1) map each relevant policy section/criterion to the evidence, (2) state what was found for each, (3) conclude whether the app aligns with the policy.

---

## Overall conclusion

Manual review required. Compare the policy excerpt and findings above to the full policy document; document alignment or gaps per criterion and state a final conclusion. To get LLM-generated reasoning and overall conclusion, set OPENAI_API_KEY or run Ollama locally (--llm ollama).
"""


class RiskScorer:
    """Score app risk 0-100 from scraped signals. No LLM needed."""

    HIGH_RISK_KEYWORDS = (
        "guaranteed return", "replica", "counterfeit", "hack", "crack", "keygen",
        "weed", "marijuana", "cannabis dispensary", "cbd oil for sale",
        "casino", "gambling", "betting odds", "slot machine",
        "forex signal", "binary option", "crypto pump", "get rich quick",
        "adult content", "escort", "onlyfans clone", "nsfw",
        "fake id", "fake passport", "buy followers", "buy likes",
        "weapon", "firearm", "ammunition", "gun shop",
    )

    MEDIUM_RISK_KEYWORDS = (
        "dropshipping", "affiliate", "mlm", "network marketing",
        "weight loss miracle", "diet pill", "supplement",
        "loan", "payday", "debt collection",
        "vpn", "proxy", "anonymous",
        "telegram bot", "whatsapp bot",
    )

    SAFE_CATEGORIES = (
        "productivity", "tracking", "dashboard", "management", "crm",
        "portfolio", "calculator", "planner", "scheduler", "organizer",
        "education", "learning", "tutorial", "course platform",
        "blog", "cms", "content management",
        "inventory", "invoicing", "booking", "appointment",
    )

    @classmethod
    def score(cls, scraped: str, fields: Optional[dict] = None,
              js_security: Optional[dict] = None, legal_info: Optional[dict] = None) -> dict:
        """Return {score, verdict, color, flags, category} from all evidence layers."""
        fields = fields or _extract_fields_from_scraped(scraped or "")
        js_security = js_security or {}
        legal_info = legal_info or {}
        text = (scraped or "").lower()
        desc = (fields.get("user_description") or fields.get("app_description")
                or fields.get("meta_description") or "").lower()
        combined = text + " " + desc

        pts = 0
        flags: list[str] = []

        # --- High-risk keyword scan (max +50) ---
        for kw in cls.HIGH_RISK_KEYWORDS:
            if kw in combined:
                pts += 50
                flags.append(f"High-risk keyword: \"{kw}\"")
                break

        # --- Medium-risk keyword scan (max +30) ---
        med_found = [kw for kw in cls.MEDIUM_RISK_KEYWORDS if kw in combined]
        if med_found:
            pts += min(len(med_found) * 10, 30)
            flags.append(f"Medium-risk keywords: {', '.join(med_found[:5])}")

        # --- Auth signals (+10) ---
        auth_cfg = (fields.get("auth_config") or "").lower()
        if auth_cfg:
            has_social = any(f'"enable_{p}_login": true' in auth_cfg
                            for p in ("google", "microsoft", "facebook", "apple"))
            email_only = '"enable_username_password": true' in auth_cfg and not has_social
            if email_only:
                pts += 10
                flags.append("Auth: email/password only (no social login)")

        # --- JS forensics: payment bypass (+25), crypto (+20), miners (+40) ---
        if js_security.get("payment_bypass"):
            platform_payments = (fields.get("payment_signals") or "").lower()
            if "payment" not in platform_payments:
                pts += 25
                flags.append(f"Payment bypass: {'; '.join(js_security['payment_bypass'][:3])}")
        if js_security.get("crypto"):
            pts += 20
            flags.append(f"Crypto/Web3 signals: {'; '.join(js_security['crypto'][:3])}")
        if js_security.get("miners"):
            pts += 40
            flags.append(f"Crypto miner detected: {'; '.join(js_security['miners'][:3])}")
        if js_security.get("obfuscation"):
            pts += 10
            flags.append("Heavily obfuscated JS code detected")

        # --- Fallback: inline payment bypass detection if js_security not provided ---
        if not js_security.get("payment_bypass"):
            if "stripe.js" in text or "stripe.com/v3" in text:
                if "payment" not in (fields.get("payment_signals") or "").lower():
                    pts += 25
                    flags.append("Stripe.js detected but no platform payment signal — possible bypass")
            if any(w in text for w in ("coinhive", "crypto-loot", "coin-hive")):
                pts += 40
                flags.append("Crypto miner script detected")

        # --- Legal pages (+15 if no legal pages, -5 if company info present) ---
        if legal_info:
            if not legal_info.get("pages_found"):
                pts += 15
                flags.append("No legal pages found (/privacy-policy, /terms)")
            else:
                flags.append(f"Legal pages present: {', '.join(legal_info['pages_found'])}")
                if not legal_info.get("has_company_name") and not legal_info.get("has_address"):
                    pts += 5
                    flags.append("Legal pages lack company name and address")
                else:
                    pts = max(0, pts - 5)
                    if legal_info.get("has_company_name"):
                        flags.append("Company name found in legal pages")
                    if legal_info.get("has_address"):
                        flags.append("Physical address found in legal pages")

        # --- Domain / freshness (+20 <7d, +10 <30d) ---
        created = fields.get("created_date", "")
        if created:
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00").split(".")[0])
                age_days = (datetime.now() - created_dt).days
                if age_days < 7:
                    pts += 20
                    flags.append(f"App created {age_days} day(s) ago — very new")
                elif age_days < 30:
                    pts += 10
                    flags.append(f"App created {age_days} days ago — recent")
                else:
                    flags.append(f"App age: {age_days} days")
            except Exception:
                pass

        # --- Content quality (+15 if empty, +5 if minimal) ---
        content_len = len(scraped or "")
        if content_len < 100:
            pts += 15
            flags.append("Very little content scraped — possible shell/front")
        elif content_len < 300:
            pts += 5
            flags.append("Minimal content scraped")

        # --- Safe category detection (reduces score up to -15) ---
        for cat in cls.SAFE_CATEGORIES:
            if cat in combined:
                pts = max(0, pts - 15)
                break

        # --- Monetization ---
        has_pricing = any(w in combined for w in ("pricing", "checkout", "$", "€", "subscribe", "buy now", "add to cart"))
        has_payment_fn = bool(fields.get("payment_functions") or fields.get("payment_signals"))
        if not has_pricing and not has_payment_fn:
            pts = max(0, pts - 10)

        # --- Integration risk ---
        integrations = (fields.get("integrations") or "").lower()
        if any(risky in integrations for risky in ("telegram", "whatsapp")):
            pts += 15
            flags.append(f"Risky integration: {integrations[:80]}")

        # --- Classify ---
        if desc:
            if any(w in desc for w in ("track", "manage", "monitor", "dashboard", "calculator", "plan")):
                category = "Utility / Productivity"
            elif any(w in desc for w in ("shop", "store", "sell", "product", "ecommerce")):
                category = "E-commerce"
            elif any(w in desc for w in ("book", "appointment", "schedule", "reserve")):
                category = "Booking / Services"
            elif any(w in desc for w in ("learn", "course", "education", "tutorial")):
                category = "Education"
            elif any(w in desc for w in ("blog", "content", "news", "media")):
                category = "Content / Media"
            elif any(w in desc for w in ("social", "community", "chat", "forum")):
                category = "Social / Community"
            else:
                category = "Other"
        else:
            category = "Unknown"

        # --- Verdict ---
        pts = max(0, min(100, pts))
        if pts < 20:
            verdict = "Low Risk — Approve"
            color = "green"
        elif pts <= 70:
            verdict = "Medium Risk — Manual Review"
            color = "orange"
        else:
            verdict = "High Risk — Decline"
            color = "red"

        if not flags:
            flags.append("No risk signals detected")

        return {
            "score": pts,
            "verdict": verdict,
            "color": color,
            "flags": flags,
            "category": category,
            "has_payments": has_pricing or has_payment_fn,
        }


def _get_llm_risk_assessment(scraped: str, fields: dict, llm_mode: str) -> Optional[dict]:
    """Ask LLM for structured risk assessment JSON. Returns parsed dict or None."""
    if llm_mode == "none":
        return None
    desc = fields.get("user_description") or fields.get("app_description") or fields.get("meta_description") or ""
    prompt = f"""You are a Compliance Risk Officer for a Payment Facilitator. Analyze the following website content for violations of Terms of Service.

Valid Business Types: SaaS, E-commerce, Blogs, Productivity, Education, Booking, Services.
Prohibited Categories: Adult content, Weapons, Gambling, IP Infringement, Crypto/Forex schemes, Drug paraphernalia, Fake documents, Counterfeit goods.

App description: {desc[:500]}

Scraped content (first 3000 chars):
{(scraped or '')[:3000]}

Output ONLY valid JSON (no markdown, no explanation):
{{"business_category": "<category>", "risk_score": <0-100>, "flags": ["<flag1>", ...], "verdict": "APPROVE|REVIEW|DECLINE", "reasoning": "<1-2 sentences>"}}"""

    raw = None
    if llm_mode in ("openai", "auto"):
        raw = _call_openai(prompt, "gpt-4o-mini", 0)
    if (not raw or len((raw or "").strip()) < 10) and llm_mode in ("ollama", "auto"):
        raw = _call_ollama(prompt, "llama3.1:8b", 0)
    if not raw:
        return None
    try:
        cleaned = re.sub(r'^```json\s*', '', raw.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None


def run_standalone_uw(url: str, policy_path: Optional[Path] = None, llm_mode: str = "none", scrape_deep: bool = False) -> dict:
    """
    Full compliance analysis pipeline: scrape → forensics → risk score → LLM assessment → verdict.
    Returns enriched dict with risk_score, risk_verdict, risk_color, risk_flags, screenshot_path, etc.
    """
    url = (url or "").strip()
    if not url or not url.startswith("http"):
        return {"error": "Invalid URL"}
    policy_path = policy_path or (PROJECT_ROOT / "policy" / "policy-excerpt.txt")
    if not policy_path.exists():
        return {"error": "Policy file not found", "scraped": None, "app_summary": None, "policy_conclusion": None}
    policy = load_policy(policy_path)

    # --- Phase 1: Data collection (parallel) ---
    scraped = None
    js_security: dict = {}
    legal_info: dict = {}
    screenshot_path: Optional[str] = None

    with ThreadPoolExecutor(max_workers=4) as pool:
        scrape_future = pool.submit(scrape_app_url, url, 15, 15000, scrape_deep)
        forensics_future = pool.submit(_scan_js_security_signals, url, 3)
        legal_future = pool.submit(_probe_legal_pages, url, 3)
        screenshot_future = pool.submit(_capture_screenshot, url) if scrape_deep else None

        scraped = scrape_future.result()
        js_security = forensics_future.result() or {}
        legal_info = legal_future.result() or {}
        screenshot_path = screenshot_future.result() if screenshot_future else None

    fields = _extract_fields_from_scraped(scraped or "")
    sources = ["Public app content: " + ("yes (scraped)" if scraped else "no")]
    if scrape_deep and scraped:
        sources[0] += " + deep"
    if js_security and any(js_security.get(k) for k in ("payment_bypass", "crypto", "miners")):
        sources.append("JS forensics: signals found")
    if legal_info.get("pages_found"):
        sources.append(f"Legal pages: {', '.join(legal_info['pages_found'])}")
    if screenshot_path:
        sources.append(f"Screenshot: {screenshot_path}")
    sources_checked = " | ".join(sources)

    # --- Phase 2: Risk scoring (rule-based, instant) ---
    risk = RiskScorer.score(scraped or "", fields=fields, js_security=js_security, legal_info=legal_info)

    # --- Phase 3: LLM assessment (if available) ---
    llm_assessment = _get_llm_risk_assessment(scraped or "", fields, llm_mode)
    if llm_assessment:
        llm_score = llm_assessment.get("risk_score", 0)
        rule_score = risk["score"]
        risk["score"] = round((rule_score * 0.4 + llm_score * 0.6))
        risk["score"] = max(0, min(100, risk["score"]))
        if llm_assessment.get("flags"):
            risk["flags"].extend([f"LLM: {f}" for f in llm_assessment["flags"][:5]])
        if llm_assessment.get("business_category"):
            risk["category"] = llm_assessment["business_category"]
        if llm_assessment.get("reasoning"):
            risk["llm_reasoning"] = llm_assessment["reasoning"]
        llm_v = (llm_assessment.get("verdict") or "").upper()
        if llm_v == "APPROVE" and risk["score"] < 30:
            risk["verdict"] = "Low Risk — Approve"
            risk["color"] = "green"
        elif llm_v == "DECLINE" or risk["score"] > 70:
            risk["verdict"] = "High Risk — Decline"
            risk["color"] = "red"
        else:
            risk["verdict"] = "Medium Risk — Manual Review"
            risk["color"] = "orange"

    # --- Phase 4: Traditional UW pipeline (app summary + policy) ---
    conv = "(No conversation — app not in list. Standalone URL check.)"
    app_summary = get_app_summary("Standalone URL", url, conv, scraped, llm_mode, "gpt-4o-mini", 0)
    policy_conclusion = get_policy_conclusion(app_summary, policy, llm_mode, "gpt-4o-mini", 0)
    base44_app_id, base44_app_name = _parse_base44_api_from_scraped(scraped or "")

    m = re.search(r"\*\*Step 3 — Verdict:\*\*\s*\n?([^\n]+)", policy_conclusion)
    policy_verdict = m.group(1).strip() if m else "Manual Review Required"
    m = re.search(r"\*\*Reasoning:\*\*\s*(.+?)(?=\n\*\*Non-compliant|\Z)", policy_conclusion, re.DOTALL)
    reasoning = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Non-compliant subcategories:\*\*\s*(.+)", policy_conclusion, re.DOTALL)
    non_compliant = (m.group(1).strip() if m else "").strip()

    return {
        "scraped": scraped,
        "app_summary": app_summary,
        "policy_conclusion": policy_conclusion,
        "verdict": policy_verdict,
        "reasoning": reasoning,
        "non_compliant_subcategories": non_compliant,
        "sources_checked": sources_checked,
        "base44_app_id": base44_app_id,
        "base44_app_name": base44_app_name,
        "risk_score": risk["score"],
        "risk_verdict": risk["verdict"],
        "risk_color": risk["color"],
        "risk_flags": risk["flags"],
        "risk_category": risk["category"],
        "has_payments": risk.get("has_payments", False),
        "llm_assessment": llm_assessment,
        "js_security": js_security,
        "legal_info": legal_info,
        "screenshot_path": screenshot_path,
    }


def ask_question_about_app(
    question: str,
    scraped_content: str,
    app_summary: str,
    policy_excerpt: Optional[str] = None,
) -> str:
    """
    Answer a question about the app using scraped content and app summary as context.
    Uses LLM (auto: OpenAI then Ollama). Returns answer or error message if no LLM available.
    """
    question = (question or "").strip()
    if not question:
        return "Please enter a question."
    context = f"""**App summary:**
{app_summary or "(none)"}

**Scraped content (what the app actually shows — pages, images, text, products):**
{(scraped_content or "(none)")[:12000]}{"..." if len(scraped_content or "") > 12000 else ""}
"""
    policy_block = ""
    if policy_excerpt:
        policy_block = f"""

**Policy excerpt (use only when the question asks about compliance, allowed/restricted, or policy rules):**
{policy_excerpt[:2000]}
"""
    prompt = f"""You are an analyst answering questions about an app. Use ONLY the evidence below. Do not invent.

**Important:** If the question is about the app's CONTENT — what it shows, sells, displays, or contains (e.g. "Is real marijuana sold or just images?", "What products are listed?") — answer from the scraped content and app summary. Do NOT use policy to infer what the app sells. Ground your answer in what the content actually shows.

If the question is about POLICY or compliance (e.g. "Is this allowed?", "Does this violate policy?"), then use the policy excerpt as well.

{context}
{policy_block}
---

**Question:** {question}

**Answer (cite evidence from content when answering about what the app shows):**"""
    out = _call_openai(prompt, "gpt-4o-mini", 0)
    if not out or len(out.strip()) < 20:
        out = _call_ollama(prompt, "llama3.1:8b", 0)
    if out and len(out.strip()) > 20:
        return out.strip()
    return "LLM not available. Set OPENAI_API_KEY in .env or run Ollama (ollama pull llama3.1:8b) to ask questions."


def get_conclusion(
    prompt: str,
    policy: str,
    summary: str,
    scraped: Optional[str],
    llm_mode: str,
    model: str,
    delay: float,
) -> str:
    """Get conclusion: try OpenAI, then Ollama, then template. llm_mode: openai | ollama | auto | none."""
    if llm_mode in ("openai", "auto"):
        out = _call_openai(prompt, model, delay)
        if out:
            return out
    if llm_mode in ("ollama", "auto"):
        ollama_model = "llama3.1:8b" if model.startswith("gpt") else model
        out = _call_ollama(prompt, ollama_model, delay)
        if out:
            return out
    return _template_conclusion(policy, summary, scraped)


def write_conclusion(
    out_dir: Path,
    app_id: str,
    app_name: str,
    app_url: Optional[str],
    conclusion: str,
    sources_checked: str = "Conversation summary; Public app content: no",
    raw_conversation_summary: Optional[str] = None,
    raw_app_content: Optional[str] = None,
):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_id = (app_id or "unknown").replace("/", "_")
    path = out_dir / f"conclusion_{safe_id}.md"
    header = f"""# Underwriting conclusion — {app_id}
**App name:** {app_name}
**App URL:** {app_url or 'N/A'}
**Date:** {date.today().isoformat()}
**Sources checked:** {sources_checked}

---

"""
    conv = (raw_conversation_summary or "").strip() or "(none)"
    app = (raw_app_content or "").strip() or "(not scraped or unavailable)"
    max_len = 50000
    if len(conv) > max_len:
        conv = conv[:max_len] + "\n\n[... truncated ...]"
    if len(app) > max_len:
        app = app[:max_len] + "\n\n[... truncated ...]"
    raw_section = """## Evidence

**Fetched conversation (full text for manual review):**
"""
    raw_section += conv + "\n\n**Scraped website / app content (full text for manual review):**\n\n" + app + "\n\n---\n\n"
    path.write_text(header + raw_section + conclusion, encoding="utf-8")
    return path


def _get_cached_app_summary(cache_dir: Path, app_id: str) -> Optional[str]:
    """Return cached app summary if exists, else None."""
    if not cache_dir or not app_id:
        return None
    safe_id = (app_id or "").replace("/", "_")
    path = cache_dir / f"app_summary_{safe_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data.get("app_summary") or "").strip() or None
    except Exception:
        return None


def _save_cached_app_summary(cache_dir: Path, app_id: str, app_summary: str, app_name: str = "", app_url: str = "") -> None:
    """Save app summary to cache for reuse."""
    if not cache_dir or not app_summary:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_id = (app_id or "unknown").replace("/", "_")
    path = cache_dir / f"app_summary_{safe_id}.json"
    try:
        path.write_text(
            json.dumps({"app_summary": app_summary, "app_name": app_name, "app_url": app_url or ""}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def write_run_manifest(out_dir: Path, run_id: str, app_ids: list, started_at: str, finished_at: str):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "app_count": len(app_ids),
        "app_ids": app_ids,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description="Run underwriting: apps + policy → LLM → conclusion files")
    p.add_argument("--apps", type=Path, default=PROJECT_ROOT / "data" / "sample_apps_from_trino.json", help="JSON or CSV of apps (must include conversation_summary, app_id, app_name, app_url)")
    p.add_argument("--policy", type=Path, default=PROJECT_ROOT / "policy" / "policy-excerpt.txt", help="Policy excerpt text file")
    p.add_argument("--out", type=Path, default=PROJECT_ROOT / "output", help="Output directory for conclusion .md files")
    p.add_argument("--model", default="gpt-4o-mini", help="OpenAI model")
    p.add_argument("--skip-llm", action="store_true", help="Only list apps that would be processed (no API call)")
    p.add_argument("--llm", choices=("auto", "openai", "ollama", "none"), default="auto", help="LLM to use: auto (try OpenAI then Ollama then template), openai, ollama, or none (template only; no API key needed)")
    p.add_argument("--delay", type=float, default=0.5, help="Seconds to wait after each LLM call (reduce rate limit risk; default 0.5)")
    p.add_argument("--scrape", action="store_true", default=True, help="Fetch each app_url and include landing-page text (default: True)")
    p.add_argument("--no-scrape", action="store_true", dest="no_scrape", help="Do not scrape app URLs")
    p.add_argument("--scrape-deep", action="store_true", dest="scrape_deep", help="Deep scrape: capture API responses and follow internal links (slower)")
    p.add_argument("--workers", type=int, default=1, help="Number of parallel workers (default 1). Use 4–8 for faster batch runs.")
    p.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / "output" / "uw_cache", help="Directory to cache app summaries (middleman). Reused on re-runs to skip middleman step.")
    p.add_argument("--no-cache", action="store_true", dest="no_cache", help="Ignore and overwrite cached app summaries")
    p.add_argument("--run-id", type=str, default=None, help="Run identifier; if set, conclusions go to out/run_<run_id>/ and a manifest.json is written")
    args = p.parse_args()

    apps = load_apps(args.apps)
    policy = load_policy(args.policy)

    # Dedupe by app_id, keep one row per app (e.g. latest summary)
    by_app: Dict[str, dict] = {}
    for row in apps:
        aid = row.get("app_id")
        if aid and (aid not in by_app or (row.get("conversation_summary") and not by_app[aid].get("conversation_summary"))):
            by_app[aid] = row

    apps = list(by_app.values())
    out_dir = Path(args.out)
    if args.run_id:
        out_dir = out_dir / f"run_{args.run_id}"
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M")
    started_at = datetime.now().isoformat()
    do_scrape = not getattr(args, "no_scrape", False)  # scrape by default; use --no-scrape to disable
    scrape_deep = getattr(args, "scrape_deep", False)

    workers = max(1, int(getattr(args, "workers", 1)))
    cache_dir = Path(getattr(args, "cache_dir", PROJECT_ROOT / "output" / "uw_cache"))
    use_cache = not getattr(args, "no_cache", False)
    print(f"Processing {len(apps)} app(s). Output: {out_dir}. Scrape: {do_scrape}{' (deep)' if scrape_deep else ''}. Workers: {workers}. Cache: {'on' if use_cache else 'off'}. Two-step: middleman → policy verdict.")

    def _process_one(app: dict) -> Optional[str]:
        """Process one app; returns app_id on success, None on skip."""
        app_id = app.get("app_id", "unknown")
        app_name = app.get("app_name", "")
        app_url = app.get("app_url")
        summary = app.get("conversation_summary") or ""

        scraped = None
        sources_checked = "Conversation summary; Public app content: no"
        if do_scrape and app_url:
            scraped = scrape_app_url(app_url, deep=scrape_deep)
            if scraped:
                sources_checked = "Conversation summary; Public app content: yes (landing page)" + (" + API + links" if scrape_deep else "")
            else:
                sources_checked = "Conversation summary; Public app content: no (unavailable or error)"

        if args.skip_llm:
            return None

        # Use saved app summary: input JSON, then cache, then compute
        app_summary = (app.get("app_summary") or "").strip() or None
        if not app_summary and use_cache:
            app_summary = _get_cached_app_summary(cache_dir, app_id)
        if not app_summary:
            app_summary = get_app_summary(
                app_name,
                app_url,
                summary,
                scraped,
                args.llm,
                args.model,
                args.delay,
                user_description=app.get("user_description"),
                earliest_conversation_preview=app.get("earliest_conversation_preview"),
            )
            _save_cached_app_summary(cache_dir, app_id, app_summary, app_name, app_url or "")

        policy_conclusion = get_policy_conclusion(app_summary, policy, args.llm, args.model, args.delay)

        conclusion = "## App summary (middleman)\n\n" + app_summary + "\n\n---\n\n## Policy comparison and verdict\n\n" + policy_conclusion
        write_conclusion(out_dir, app_id, app_name, app_url, conclusion, sources_checked, raw_conversation_summary=summary or None, raw_app_content=scraped)
        return app_id

    processed_ids = []
    if workers <= 1:
        for app in apps:
            if args.skip_llm:
                app_id = app.get("app_id", "unknown")
                app_name = app.get("app_name", "")
                summary = app.get("conversation_summary") or ""
                scraped = scrape_app_url(app.get("app_url") or "", deep=scrape_deep) if (do_scrape and app.get("app_url")) else None
                print(f"  Would process: {app_id} {app_name} (summary length: {len(summary)}, scraped: {bool(scraped)})")
            else:
                try:
                    aid = _process_one(app)
                    if aid:
                        processed_ids.append(aid)
                        print(f"  Wrote conclusion for {aid} ({app.get('app_name', '')})")
                except Exception as e:
                    print(f"  Error processing {app.get('app_id', '?')}: {e}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_process_one, app): app for app in apps}
            for future in as_completed(futures):
                app = futures[future]
                try:
                    aid = future.result()
                    if aid:
                        processed_ids.append(aid)
                        print(f"  Wrote conclusion for {aid} ({app.get('app_name', '')})")
                except Exception as e:
                    print(f"  Error processing {app.get('app_id', '?')}: {e}")

    if not args.skip_llm and apps:
        write_run_manifest(out_dir, run_id, processed_ids, started_at, datetime.now().isoformat())
        print(f"Done. Conclusions in {out_dir}")


if __name__ == "__main__":
    main()
