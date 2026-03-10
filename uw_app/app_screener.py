"""
App screener: URL in → scrape + classify → structured verdict.

Hybrid approach:
  1. Base44 APIs for clean identity (app_id, name, description, visibility, payments)
  2. Full scrape_app_url for page content (the classifier needs real text to catch violations)
  3. Structured display: identity + signals shown cleanly, full content available on demand
"""
from __future__ import annotations
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .policy_classifier import classify

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


@dataclass
class ScreenResult:
    url: str
    screened_at: str = ""
    elapsed_seconds: float = 0.0

    # Identity (from API)
    app_id: Optional[str] = None
    app_name: Optional[str] = None
    app_description: Optional[str] = None
    visibility: Optional[str] = None

    # Structured signals (from API + scrape)
    entity_types: list[str] = field(default_factory=list)
    payment_signals: list[str] = field(default_factory=list)
    login_required: Optional[bool] = None
    login_methods: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)
    meta_title: str = ""
    meta_description: str = ""

    # Page content (for classifier + on-demand view, NOT the full raw dump)
    page_content_summary: str = ""  # cleaned ~2000 chars for display
    content_length: int = 0  # original content length for reference

    # Classification
    overall_verdict: str = ""
    overall_color: str = "gray"
    confidence: int = 0
    policy_matches: list[dict] = field(default_factory=list)
    top_category: str = ""
    top_subcategory: str = ""
    top_p_and_r_id: Optional[int] = None
    top_p_and_r_name: Optional[str] = None

    # Extras
    error: Optional[str] = None
    data_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_full_content", None)
        return d


# ── Main entry points ──────────────────────────────────────────────────────────

def screen(
    url: str,
    deep: bool = False,
    conversation_summary: str = "",
    trino_description: str = "",
    app_name_hint: str = "",
) -> ScreenResult:
    """Screen a single URL. Returns ScreenResult with verdict + evidence.

    Optional Trino context (from the UW population query):
      conversation_summary: builder conversation summary — usually more truthful than description
      trino_description:    app description from Trino (base44_apps table)
      app_name_hint:        app name from Trino (used if API doesn't return one)
    """
    t0 = time.time()
    result = ScreenResult(url=url, screened_at=datetime.now().isoformat(timespec="seconds"))

    url = (url or "").strip()
    if not url.startswith("http"):
        url = "https://" + url
    result.url = url

    full_content = ""

    try:
        is_base44 = any(d in url for d in ("base44.app", "base44.com", "velino.org"))

        # Step 1: API-first for identity (fast, structured)
        if is_base44:
            _scrape_base44_api(url, result)

        # Step 2: Full scrape for real page content (the classifier needs this)
        full_content = _scrape_full(url, result, deep=deep)

        # Step 2b: If full scrape returned nothing, try Base44 API with force
        if not full_content and not is_base44:
            _scrape_base44_api(url, result)
            full_content = _scrape_full(url, result, deep=deep)

        # Step 3: If API didn't get identity, extract from scraped content
        if full_content and not result.app_id:
            _extract_identity_from_scrape(full_content, result)

    except Exception as e:
        result.error = f"Scrape failed: {e}"
        if not result.overall_verdict:
            result.overall_verdict = "Error"
            result.overall_color = "gray"
        result.elapsed_seconds = round(time.time() - t0, 1)
        return result

    # If we still have no app name, derive from URL hostname or hint
    if not result.app_name:
        if app_name_hint:
            result.app_name = app_name_hint
        else:
            try:
                slug = urlparse(url).netloc.split(".")[0]
                result.app_name = slug.replace("-", " ").replace("_", " ").title()
            except Exception:
                pass

    # Trino description supplements (but doesn't override) scraped description
    if trino_description and not result.app_description:
        result.app_description = trino_description[:500]

    # Build summarized content for display (not the full dump)
    result.page_content_summary = _summarize_content(full_content)
    result.content_length = len(full_content)

    # Fetch content from external signal links (Google Forms, Typeform, etc.)
    # This catches cases like Rankify: "Play Now" → Google Form with "$10,000 prize" rules
    external_content = _fetch_external_signal_links(full_content, url)
    if external_content:
        result.data_sources.append("external-links")
        full_content = full_content + "\n\n" + external_content

    # Classify using ALL available text — include conversation summary from Trino
    # The conversation summary is often more revealing than the app description,
    # since it reflects what the builder actually built (e.g. "PACKMAN cannabis shop")
    trino_context = " ".join(filter(None, [conversation_summary, trino_description]))
    text_for_classifier = _build_classifier_input(result, full_content)
    classification = classify(
        text_for_classifier,
        user_description=result.app_description or "",
        extra_context=trino_context,
    )

    result.overall_verdict = classification.overall_verdict
    result.overall_color = classification.overall_color
    result.confidence = classification.confidence
    result.policy_matches = [
        {
            "category": m.category,
            "subcategory": m.subcategory,
            "verdict": m.verdict,
            "color": m.color,
            "confidence": m.confidence,
            "keywords": m.matched_keywords,
            "p_and_r_id": m.p_and_r_id,
            "p_and_r_name": m.p_and_r_name,
            "p_and_r_ids": m.p_and_r_ids,
        }
        for m in classification.matches
    ]
    if classification.top_match:
        top = classification.top_match
        result.top_category = top.category
        result.top_subcategory = top.subcategory
        result.top_p_and_r_id = top.p_and_r_id
        result.top_p_and_r_name = top.p_and_r_name

    # Override: if there's truly no content and no description, "Likely Supportable"
    # is misleading — we just don't know. Use "Insufficient Data" instead.
    # Exception: if Trino context was provided, we had something to classify against.
    has_real_content = (
        result.content_length > 50
        or bool(result.app_description)
        or bool(result.meta_description)
        or bool(result.meta_title)
        or bool(conversation_summary)
        or bool(trino_description)
    )
    if not has_real_content and not result.policy_matches:
        result.overall_verdict = "Insufficient Data"
        result.overall_color = "gray"
        result.confidence = 0

    result.elapsed_seconds = round(time.time() - t0, 1)
    return result


def screen_batch(
    urls: list[str],
    deep: bool = False,
    max_workers: int = 6,
    trino_rows: Optional[list[dict]] = None,
) -> list[ScreenResult]:
    """Screen multiple URLs in parallel.

    trino_rows: optional list of dicts with keys url, conversation_summary,
                trino_description, app_name_hint — one per URL, same order as urls.
                Provide these when you have Trino data to enrich classification.
    """
    from concurrent.futures import as_completed
    results: list[ScreenResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # Build per-URL Trino context lookup if provided
        trino_by_url: dict[str, dict] = {}
        if trino_rows:
            for row in trino_rows:
                key = (row.get("url") or "").strip().rstrip("/")
                if key:
                    trino_by_url[key] = row

        def _screen_with_context(u: str) -> ScreenResult:
            ctx = trino_by_url.get(u.strip().rstrip("/"), {})
            return screen(
                u,
                deep=deep,
                conversation_summary=ctx.get("conversation_summary", ""),
                trino_description=ctx.get("trino_description", ""),
                app_name_hint=ctx.get("app_name_hint", ""),
            )

        futures = {pool.submit(_screen_with_context, u): u for u in urls}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                r = ScreenResult(url=futures[future], error=str(e))
                r.overall_verdict = "Error"
                r.overall_color = "gray"
                results.append(r)
    return results


# ── Base44 API scraping (identity + signals) ───────────────────────────────────

def _scrape_base44_api(url: str, result: ScreenResult) -> None:
    """Hit Base44 public API endpoints for structured identity data."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    host = parsed.netloc

    app_id = _resolve_app_id(origin, host, parsed.path)
    if not app_id:
        # Try the meta tags at least — the URL hostname itself is a signal
        _scrape_meta_tags_safe(url, result)
        result.data_sources.append("api:domain-lookup-failed")
        return

    result.app_id = app_id
    result.data_sources.append("api:app-id")

    login_info = _fetch_json(f"{origin}/api/apps/public/login-info/by-id/{app_id}", timeout=4)
    if isinstance(login_info, dict):
        result.data_sources.append("api:login-info")
        _parse_login_info(login_info, result)

    config = _fetch_json(f"{origin}/api/apps/public/config", timeout=2)
    if isinstance(config, dict):
        result.data_sources.append("api:config")
        _parse_config(config, result)


def _resolve_app_id(origin: str, host: str, path: str) -> Optional[str]:
    m = re.search(r"/app/([a-f0-9]{20,})", path)
    if m:
        return m.group(1)
    try:
        raw = _fetch_text(f"{origin}/api/apps/public/prod/domain/{host}", timeout=3)
        if raw:
            raw = raw.strip().strip('"')
            try:
                data = json.loads(raw)
                if isinstance(data, str):
                    raw = data
                elif isinstance(data, dict):
                    raw = data.get("app_id") or data.get("appId") or data.get("id") or ""
            except (json.JSONDecodeError, TypeError):
                pass
            if raw and len(raw) >= 20 and raw.replace("-", "").replace("_", "").isalnum():
                return raw
    except Exception:
        pass
    return None


def _parse_login_info(data: dict, result: ScreenResult) -> None:
    result.app_name = data.get("name") or data.get("appName") or data.get("title") or result.app_name
    desc = data.get("description") or ""
    if isinstance(desc, str) and len(desc.strip()) > 3:
        result.app_description = desc.strip()[:500]

    result.visibility = data.get("visibility") or data.get("appVisibility") or data.get("access") or ""

    login_req = data.get("requireLogin") or data.get("loginRequired") or data.get("require_login")
    if login_req is not None:
        result.login_required = bool(login_req)

    methods = data.get("loginMethods") or data.get("authMethods") or []
    if isinstance(methods, list):
        result.login_methods = [str(m) for m in methods][:10]
    elif isinstance(methods, dict):
        result.login_methods = [k for k, v in methods.items() if v][:10]

    for pk in ("payments", "paymentProvider", "stripeEnabled", "wixPayments",
               "hasPayments", "paymentMethods", "stripe", "payment"):
        pv = data.get(pk)
        if pv is not None and pv not in (False, "", [], {}):
            result.payment_signals.append(f"{pk}={_compact(pv)}")

    for fk in ("features", "enabledFeatures", "publicSettings"):
        fv = data.get(fk)
        if isinstance(fv, dict):
            result.features.extend(k for k, v in fv.items() if v)
        elif isinstance(fv, list):
            result.features.extend(str(x) for x in fv[:15])

    for ik in ("integrations", "connectors", "enabledIntegrations"):
        iv = data.get(ik)
        if isinstance(iv, list):
            result.integrations.extend(str(x) for x in iv[:10])
        elif isinstance(iv, dict):
            result.integrations.extend(k for k, v in iv.items() if v)

    entities = data.get("entities") or data.get("entityTypes") or data.get("schemas")
    if isinstance(entities, list):
        result.entity_types = [str(e.get("name", e) if isinstance(e, dict) else e) for e in entities][:20]
    elif isinstance(entities, dict):
        result.entity_types = list(entities.keys())[:20]


def _parse_config(config: dict, result: ScreenResult) -> None:
    desc = config.get("description") or config.get("appDescription") or ""
    if desc and not result.app_description:
        result.app_description = str(desc)[:500]
    name = config.get("name") or config.get("appName") or ""
    if name and not result.app_name:
        result.app_name = str(name)
    for key in ("entities", "entityTypes", "schemas", "tables"):
        val = config.get(key)
        if isinstance(val, (list, dict)) and not result.entity_types:
            if isinstance(val, list):
                result.entity_types = [str(e.get("name", e) if isinstance(e, dict) else e) for e in val][:20]
            else:
                result.entity_types = list(val.keys())[:20]


def _scrape_meta_tags(url: str, result: ScreenResult) -> None:
    """Fetch just the <head> for title, description — lightweight."""
    try:
        req = Request(url, headers={"User-Agent": _UA})
        with urlopen(req, timeout=5) as resp:
            head_html = resp.read(15_000).decode("utf-8", errors="replace")

        title_m = re.search(r"<title[^>]*>([^<]+)</title>", head_html, re.IGNORECASE)
        if title_m:
            result.meta_title = title_m.group(1).strip()[:200]

        desc_m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', head_html, re.IGNORECASE)
        if not desc_m:
            desc_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']', head_html, re.IGNORECASE)
        if desc_m:
            result.meta_description = desc_m.group(1).strip()[:300]
            if not result.app_description:
                result.app_description = result.meta_description

        if not result.app_name and result.meta_title:
            result.app_name = result.meta_title.split("|")[0].split("—")[0].strip()[:100]

        result.data_sources.append("http:meta-tags")
    except Exception:
        pass


def _scrape_meta_tags_safe(url: str, result: ScreenResult) -> None:
    """Fetch meta tags if not already done."""
    if "http:meta-tags" not in result.data_sources:
        _scrape_meta_tags(url, result)


# ── External link signal fetching ──────────────────────────────────────────────

# Domains where fetching external link content is high-value for policy classification.
# These are places where operators post prize rules, entry forms, gambling T&Cs, etc.
_SIGNAL_DOMAINS = (
    "docs.google.com/forms",
    "typeform.com",
    "jotform.com",
    "forms.gle",
    "airtable.com",
    "notion.so",
    "tally.so",
    "wufoo.com",
    "surveymonkey.com",
)


def _fetch_external_signal_links(scraped_text: str, source_url: str, max_links: int = 3) -> str:
    """
    Extract external URLs from scraped content and fetch content from high-signal domains.

    Called after scraping so that any links found by Playwright (in deep mode) or
    embedded in page HTML are followed. Returns additional text to feed the classifier.

    Example: Rankify has a 'Play Now' button → Google Forms with '$10,000 prize' rules.
    The fast scraper never clicks the button; but deep mode or HTML parsing may expose
    the Google Forms URL in a 'Links:' section — this function then fetches its content.
    """
    if not scraped_text:
        return ""

    # Extract all http(s) URLs from scraped text
    found_urls = re.findall(r'https?://[^\s"\'<>,;]+', scraped_text)

    # Also scan raw HTML of the source page for hrefs (catches static links in shell)
    try:
        req = Request(source_url, headers={"User-Agent": _UA})
        with urlopen(req, timeout=4) as resp:
            raw_html = resp.read(30_000).decode("utf-8", errors="replace")
        html_links = re.findall(r'href=["\']?(https?://[^"\'>\s]+)', raw_html)
        found_urls = list(dict.fromkeys(found_urls + html_links))  # deduplicate, preserve order
    except Exception:
        pass

    # Filter to signal domains only
    signal_urls = [
        u for u in found_urls
        if any(d in u for d in _SIGNAL_DOMAINS)
    ][:max_links]

    if not signal_urls:
        return ""

    extra_parts = []
    for ext_url in signal_urls:
        try:
            req = Request(ext_url, headers={"User-Agent": _UA, "Accept": "text/html"})
            with urlopen(req, timeout=5) as resp:
                html = resp.read(40_000).decode("utf-8", errors="replace")
            # Strip tags, collapse whitespace
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 100:
                extra_parts.append(f"[External link: {ext_url}]\n{text[:2000]}")
        except Exception:
            extra_parts.append(f"[External link found: {ext_url}]")

    return "\n\n".join(extra_parts)


# ── Full content scraping ──────────────────────────────────────────────────────

def _scrape_full(url: str, result: ScreenResult, deep: bool = False) -> str:
    """
    Scrape real page content using the proven scrape_app_url.
    Returns the full text for the classifier. Also extracts extra signals.
    """
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from run_underwriting import scrape_app_url

    scraped = scrape_app_url(url, timeout_seconds=12, deep=deep) or ""
    if scraped:
        result.data_sources.append("scrape:full" if deep else "scrape:fast")

        # Extract extra entity types from page content
        entities = re.findall(r"Entity '(\w+)'", scraped)
        new_ents = set(entities) - set(result.entity_types)
        if new_ents:
            result.entity_types.extend(sorted(new_ents)[:10])

        # Extract payment signals from content
        for kw in ("stripe", "payment", "checkout", "subscribe", "invoice"):
            if kw in scraped.lower() and kw not in " ".join(result.payment_signals).lower():
                result.payment_signals.append(kw)

        # Extract meta from content if API didn't get it
        if not result.meta_title:
            m = re.search(r"Title:\s*([^\n|]+)", scraped)
            if m:
                result.meta_title = m.group(1).strip()[:200]
        if not result.meta_description:
            m = re.search(r"Description:\s*([^\n]+)", scraped)
            if m:
                result.meta_description = m.group(1).strip()[:300]

    return scraped


def _extract_identity_from_scrape(scraped: str, result: ScreenResult) -> None:
    """Fallback: extract identity fields from scraped text if API missed them."""
    if not result.app_id:
        m = re.search(r"App ID \(from Base44 API\):\s*([a-f0-9]{24})", scraped)
        if m:
            result.app_id = m.group(1)
    if not result.app_name:
        m = re.search(r"App name \(from Base44 API\):\s*(.+?)(?:\n|$)", scraped)
        if m:
            result.app_name = m.group(1).strip()
    if not result.app_description:
        m = re.search(r"App description:\s*(.+?)(?:\n|$)", scraped)
        if m:
            result.app_description = m.group(1).strip()[:500]


# ── Content processing ─────────────────────────────────────────────────────────

def _summarize_content(full_content: str) -> str:
    """
    Create a human-readable summary of scraped content.
    Extracts the meaningful sections, strips boilerplate/noise.
    """
    if not full_content:
        return ""

    sections = []

    # Extract structured sections (API data, meta tags, config)
    for label, pattern in [
        ("API", r"\[Base44 public API[^\]]*\]\s*(.*?)(?:\n\n\[|\Z)"),
        ("Meta", r"\[SEO meta tags\]\s*(.*?)(?:\n\n|\Z)"),
        ("Config", r"\[Platform config\]\s*(.*?)(?:\n\n|\Z)"),
    ]:
        m = re.search(pattern, full_content, re.DOTALL)
        if m:
            text = m.group(1).strip()
            if text:
                sections.append(f"[{label}]\n{text[:600]}")

    # Extract entity info
    entity_lines = re.findall(r"Entity '(\w+)'[^\n]*", full_content)
    if entity_lines:
        sections.append(f"[Entities] {', '.join(set(entity_lines[:10]))}")

    # Extract meaningful page text (skip the boilerplate)
    # Remove the structured sections we already captured
    page_text = full_content
    for pattern in [
        r"\[Base44 public API[^\]]*\].*?(?=\n\n\[|\Z)",
        r"\[SEO meta tags\].*?(?=\n\n|\Z)",
        r"\[Platform config\].*?(?=\n\n|\Z)",
        r"\[Sitemap pages\].*?(?=\n\n|\Z)",
    ]:
        page_text = re.sub(pattern, "", page_text, flags=re.DOTALL)

    # Clean remaining text
    page_text = re.sub(r"\s+", " ", page_text).strip()
    if page_text and len(page_text) > 50:
        sections.append(f"[Page text]\n{page_text[:800]}")

    return "\n\n".join(sections)[:2500]


def _build_classifier_input(result: ScreenResult, full_content: str) -> str:
    """Build text for the policy classifier — uses ALL available content."""
    parts = []

    # URL hostname is a huge signal: "loan-pay-easy" → "loan pay easy"
    try:
        host = urlparse(result.url).netloc.split(":")[0]
        slug = host.split(".")[0]  # e.g. "loan-pay-easy"
        slug_words = slug.replace("-", " ").replace("_", " ")
        parts.append(slug_words)
    except Exception:
        pass

    # Structured fields (high signal)
    if result.app_name:
        parts.append(result.app_name)
    if result.app_description:
        parts.append(result.app_description)
    if result.meta_title:
        parts.append(result.meta_title)
    if result.meta_description:
        parts.append(result.meta_description)
    if result.entity_types:
        parts.append("entities: " + " ".join(result.entity_types))
    if result.payment_signals:
        parts.append("payment: " + " ".join(result.payment_signals))
    if result.features:
        parts.append("features: " + " ".join(result.features))
    if result.integrations:
        parts.append("integrations: " + " ".join(result.integrations))

    # Full scraped content (the classifier needs this for keyword matching)
    if full_content:
        parts.append(full_content[:8000])

    return " ".join(parts)


# ── Utilities ──────────────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: int = 3) -> Optional[dict]:
    try:
        req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _fetch_text(url: str, timeout: int = 3) -> Optional[str]:
    try:
        req = Request(url, headers={"User-Agent": _UA})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _compact(val, max_len: int = 80) -> str:
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, (str, int, float)):
        return str(val)[:max_len]
    return json.dumps(val, default=str)[:max_len]
