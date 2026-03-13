"""
Web scraper: URL in → page content out.

Architecture-aware layered strategy:
  - Base44 apps: public API + meta tags + optional Playwright
  - Other URLs: urllib → Playwright fallback

Extracted from run_underwriting.py to decouple the screener from the LLM pipeline.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── HTML / meta extraction ────────────────────────────────────────────────────

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
    """Fetch sitemap.xml and return list of page URLs."""
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
    """Detect if a page is a Base44 app from HTML markers."""
    if not html:
        return False
    indicators = [
        "base44.app", "base44.com", "__BASE44__", "base44-apps.com",
        "base44Client", "@base44/sdk", "base44_",
    ]
    html_lower = html.lower()
    return sum(1 for i in indicators if i.lower() in html_lower) >= 2


# ── Frontend / JS scanning ────────────────────────────────────────────────────

def _fetch_frontend_config(url: str, timeout: int = 2) -> str:
    """Fetch /api/frontend-config.js and extract platform signals."""
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
    """Discover JS bundles, scan for app-specific API/entity/function references."""
    try:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        scripts = re.findall(r'<script[^>]+src=["\']([^"\']+\.js)["\']', html, re.IGNORECASE)
        parts: list[str] = []

        skip_names = ("framework.", "main.", "badge.", "google", "tailwind", "gsi/client")
        app_chunks = []
        for s in scripts:
            if not any(skip in s.lower() for skip in skip_names):
                full = s if s.startswith("http") else f"{origin}{s}"
                app_chunks.append(full)

        if not app_chunks:
            return ""

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
    """Probe for /privacy-policy, /terms, /legal pages."""
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
    """Take a full-page screenshot via Playwright."""
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


# ── Text / product extraction helpers ─────────────────────────────────────────

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
        for i, item in enumerate(obj[:20]):
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
    """Extract products/items from JSON-LD and DOM."""
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
    if base_url:
        links = _extract_links_from_html(html, base_url, max_links=30)
        if links:
            parts.append("Links: " + "; ".join(links[:20]))
    return " ".join(parts)[:max_chars] if parts else ""


# ── Playwright browser management ─────────────────────────────────────────────

_pw_instance = None
_pw_browser = None
_pw_lock = threading.Lock()

def _get_or_create_browser():
    """Singleton Playwright browser — launch once, reuse across calls."""
    global _pw_instance, _pw_browser
    with _pw_lock:
        if _pw_browser is not None:
            try:
                _pw_browser.contexts
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
    """Use Playwright to render JavaScript, capture API responses, extract text."""
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


# ── Base44 public API probing ─────────────────────────────────────────────────

def _fetch_base44_public_apis(base_url: str, timeout: int = 8, force: bool = False) -> str:
    """Base44-specific: probe known public API endpoints (no auth required)."""
    if not force and "base44.app" not in base_url and "base44.com" not in base_url:
        return ""
    try:
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
        domain = parsed.netloc
        parts: List[str] = []

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

            login_url = f"{origin}/api/apps/public/login-info/by-id/{app_id_raw}"
            try:
                req2 = Request(login_url, headers={"User-Agent": "Mozilla/5.0 (compatible; Base44-UW/1.0)"})
                with urlopen(req2, timeout=min(timeout, 2)) as r2:
                    data = json.loads(r2.read().decode("utf-8", errors="replace"))
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
            except Exception:
                pass

        return "\n".join(parts) if parts else ""
    except Exception:
        return ""


def _parse_base44_api_from_scraped(scraped: str) -> tuple[Optional[str], Optional[str]]:
    """Extract App ID and App name from scraped Base44 API section."""
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


# ── HTTP fetch helpers ────────────────────────────────────────────────────────

def _format_error_if_json(content: str) -> str:
    """If content is an error JSON, return a cleaner message."""
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
    """Fast HTTP fetch with HTML-to-text. Returns (clean_text, raw_html) or (None, None)."""
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


# ── Main entry point ──────────────────────────────────────────────────────────

def scrape_app_url(url: str, timeout_seconds: int = 15, max_chars: int = 15000, deep: bool = False) -> Optional[str]:
    """Fetch URL and return plain text. Architecture-aware layered strategy.
    Base44 apps: API + meta tags (~2s). Playwright only when deep=True.
    Other URLs: urllib (~1s) -> Playwright only if urllib fails.
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

        pw_out = _run_playwright_with_timeout(min(timeout_seconds, 10))
        return _assemble(pw_out) or fast_result

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

    pw_out = _run_playwright_with_timeout(min(timeout_seconds, 10))
    if pw_out and "enable javascript" not in (pw_out or "").lower():
        if not base44_api and "Base44 platform detected on custom domain" in (pw_out or ""):
            base44_api = _fetch_base44_public_apis(url, timeout=4, force=True)
        return _assemble(pw_out)

    fallback_text, _ = _urllib_fetch(url, timeout=timeout_seconds, max_chars=max_chars)
    if fallback_text:
        return _assemble(fallback_text)

    return _assemble(pw_out) or _assemble()
