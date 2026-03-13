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


# ── Scraper functions (extracted to uw_app/scraper.py) ─────────────────────────
# Re-exported here for backward compatibility with scripts that import from this file.
from uw_app.scraper import (
    scrape_app_url,
    _get_or_create_browser,
    _scrape_with_playwright,
    _extract_meta_tags,
    _fetch_sitemap,
    _detect_base44_from_html,
    _fetch_frontend_config,
    _scan_js_bundle_for_signals,
    _scan_js_security_signals,
    _probe_legal_pages,
    _capture_screenshot,
    _flatten_json_for_text,
    _extract_links_from_html,
    _extract_products_from_html,
    _extract_text_from_html,
    _fetch_base44_public_apis,
    _parse_base44_api_from_scraped,
    _format_error_if_json,
    _urllib_fetch,
)



# Old scraper function definitions removed — now imported from uw_app.scraper above.



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
