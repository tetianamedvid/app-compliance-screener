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
import time
from datetime import date, datetime
from typing import Dict, Optional
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


def _scrape_with_playwright(url: str, timeout_ms: int = 25000, max_chars: int = 15000) -> Optional[str]:
    """Use Playwright to render JavaScript and extract visible text. Returns None if Playwright unavailable or fails."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_ms, 15000))
                # Wait for SPA to render: retry until "enable javascript" disappears or we hit max wait
                text = ""
                for i in range(8):
                    page.wait_for_timeout(3000)
                    text = page.evaluate("""() => {
                        const body = document.body;
                        if (!body) return '';
                        return body.innerText || body.textContent || '';
                    }""")
                    if text and isinstance(text, str):
                        text = re.sub(r"\s+", " ", text).strip()
                        if not text:
                            continue
                        # Reject if we only have the create-react-app placeholder
                        if "enable javascript" in text.lower() and len(text) < 150:
                            continue
                        # Good content: either no "enable javascript" or we have substantial text
                        if "enable javascript" not in text.lower() or len(text) > 200:
                            return (text[:max_chars] + "...") if len(text) > max_chars else text
                # Final attempt: use whatever we got (even if short)
                if text and isinstance(text, str):
                    text = re.sub(r"\s+", " ", text).strip()
                    if text:
                        return (text[:max_chars] + "...") if len(text) > max_chars else text
            finally:
                browser.close()
    except Exception:
        pass
    return None


def scrape_app_url(url: str, timeout_seconds: int = 15, max_chars: int = 15000) -> Optional[str]:
    """Fetch URL and return plain text. Tries Playwright first (for JS-rendered SPAs), then urllib."""
    if not url or not url.startswith("http"):
        return None
    # 1) Try Playwright for JavaScript-rendered pages (avoids "You need to enable JavaScript")
    out = _scrape_with_playwright(url, timeout_ms=timeout_seconds * 1000, max_chars=max_chars)
    if out and "enable javascript" not in (out or "").lower():
        return out
    # 2) Fallback: urllib (fast but no JS execution)
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        with urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (URLError, HTTPError, OSError, Exception):
        return out if out else None
    raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    fallback = (raw[:max_chars] + "...") if len(raw) > max_chars else raw if raw else None
    return fallback if fallback else out


def build_middleman_prompt(app_name: str, app_url: Optional[str], conversation_summary: str, scraped_content: Optional[str]) -> str:
    body = _extract_prompt_body(MIDDLEMAN_TEMPLATE)
    scraped = scraped_content if scraped_content else "Not available (app not scraped or not public)."
    if app_url and not scraped_content:
        scraped = f"App URL: {app_url}. Content: Not available (not scraped)."
    return body.replace("[app_name]", app_name or "Unknown").replace(
        "[app_url]", app_url or "N/A"
    ).replace("[conversation_summary]", conversation_summary or "(No conversation summary available)").replace(
        "[scraped_content]", scraped
    )


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


def _call_ollama(prompt: str, model: str, delay_after_seconds: float, timeout_seconds: int = 600) -> Optional[str]:
    """Call local Ollama (no API key). Requires Ollama running with the given model."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode("utf-8")
    for attempt in range(3):
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
            if attempt < 2:
                time.sleep(5)
            continue
    return None


def _template_app_summary(conversation_summary: str, scraped_content: Optional[str]) -> str:
    """No-LLM mode: full conversation logs and full scraped website text for manual review."""
    max_len = 50000
    scraped = (scraped_content or "").strip() or "(no scraped content)"
    if len(scraped) > max_len:
        scraped = scraped[:max_len] + "\n\n[... truncated ...]"
    conv = (conversation_summary or "").strip() or "(no conversation log)"
    if len(conv) > max_len:
        conv = conv[:max_len] + "\n\n[... truncated ...]"
    return f"""# App Summary (Manual Review)

**Scraped Content:**
{scraped}

**Conversation Log:**
{conv}
"""


def _template_policy_verdict() -> str:
    """No-LLM mode: verdict MUST be Manual Review Required, not Restricted."""
    return """**Step 1 — What is sold:**  
See App Summary (Manual Review) above.

**Step 2 — Comparison to policy:**  
Manual comparison required. Map app summary to policy categories and subcategories.

**Step 3 — Verdict:** Manual Review Required  
**Reasoning:** Automatic analysis disabled (No LLM). Please review the evidences below manually.  
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
**Reasoning:** Automatic analysis disabled (No LLM). Please review the evidences below manually.  
**Non-compliant subcategories:** Unknown."""


def get_app_summary(
    app_name: str,
    app_url: Optional[str],
    conversation_summary: str,
    scraped_content: Optional[str],
    llm_mode: str,
    model: str,
    delay: float,
) -> str:
    """Middleman step: summarize intent, what is sold, what buyer gets."""
    prompt = build_middleman_prompt(app_name, app_url, conversation_summary, scraped_content)
    if llm_mode in ("openai", "auto"):
        out = _call_openai(prompt, model, delay)
        if out and len(out.strip()) > 100:
            return out.strip()
    if llm_mode in ("ollama", "auto"):
        ollama_model = "llama3.1:8b" if model.startswith("gpt") else model
        out = _call_ollama(prompt, ollama_model, delay)
        if out and len(out.strip()) > 100:
            return out.strip()
    return _template_app_summary(conversation_summary, scraped_content)


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


def run_standalone_uw(url: str, policy_path: Optional[Path] = None, llm_mode: str = "none") -> dict:
    """
    Standalone scrape + UW for a URL when app is not in the list.
    Returns dict: scraped, app_summary, policy_conclusion, verdict, reasoning, sources_checked.
    """
    url = (url or "").strip()
    if not url or not url.startswith("http"):
        return {"error": "Invalid URL"}
    policy_path = policy_path or (PROJECT_ROOT / "policy" / "policy-excerpt.txt")
    if not policy_path.exists():
        return {"error": "Policy file not found", "scraped": None, "app_summary": None, "policy_conclusion": None}
    policy = load_policy(policy_path)
    scraped = scrape_app_url(url)
    sources_checked = "Public app content: yes (scraped)" if scraped else "Public app content: no (unavailable or error)"
    conv = "(No conversation — app not in list. Standalone URL check.)"
    app_summary = get_app_summary("Standalone URL", url, conv, scraped, llm_mode, "gpt-4o-mini", 0)
    policy_conclusion = get_policy_conclusion(app_summary, policy, llm_mode, "gpt-4o-mini", 0)
    # Parse verdict and reasoning for UI
    import re
    m = re.search(r"\*\*Step 3 — Verdict:\*\*\s*\n?([^\n]+)", policy_conclusion)
    verdict = m.group(1).strip() if m else "Manual Review Required"
    m = re.search(r"\*\*Reasoning:\*\*\s*(.+?)(?=\n\*\*Non-compliant|\Z)", policy_conclusion, re.DOTALL)
    reasoning = m.group(1).strip() if m else ""
    m = re.search(r"\*\*Non-compliant subcategories:\*\*\s*(.+)", policy_conclusion, re.DOTALL)
    non_compliant = (m.group(1).strip() if m else "").strip()
    return {
        "scraped": scraped,
        "app_summary": app_summary,
        "policy_conclusion": policy_conclusion,
        "verdict": verdict,
        "reasoning": reasoning,
        "non_compliant_subcategories": non_compliant,
        "sources_checked": sources_checked,
    }


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

    print(f"Processing {len(apps)} app(s). Output: {out_dir}. Scrape: {do_scrape}. Two-step: middleman → policy verdict.")

    processed_ids = []
    for app in apps:
        app_id = app.get("app_id", "unknown")
        app_name = app.get("app_name", "")
        app_url = app.get("app_url")
        summary = app.get("conversation_summary") or ""

        scraped = None
        sources_checked = "Conversation summary; Public app content: no"
        if do_scrape and app_url:
            scraped = scrape_app_url(app_url)
            if scraped:
                sources_checked = "Conversation summary; Public app content: yes (landing page)"
            else:
                sources_checked = "Conversation summary; Public app content: no (unavailable or error)"

        if args.skip_llm:
            print(f"  Would process: {app_id} {app_name} (summary length: {len(summary)}, scraped: {bool(scraped)})")
            continue

        # Step 1: Middleman — summarize intent, what is sold, what buyer gets
        app_summary = get_app_summary(app_name, app_url, summary, scraped, args.llm, args.model, args.delay)
        # Step 2: Policy comparison — what is sold → compare to policy → Allowed/Restricted/Not-allowed + reasoning + non-compliant subcategories
        policy_conclusion = get_policy_conclusion(app_summary, policy, args.llm, args.model, args.delay)

        conclusion = "## App summary (middleman)\n\n" + app_summary + "\n\n---\n\n## Policy comparison and verdict\n\n" + policy_conclusion
        write_conclusion(out_dir, app_id, app_name, app_url, conclusion, sources_checked, raw_conversation_summary=summary or None, raw_app_content=scraped)
        processed_ids.append(app_id)
        print(f"  Wrote conclusion for {app_id} ({app_name})")

    if not args.skip_llm and apps:
        write_run_manifest(out_dir, run_id, processed_ids, started_at, datetime.now().isoformat())
        print(f"Done. Conclusions in {out_dir}")


if __name__ == "__main__":
    main()
