"""
LLM-based classification fallback for apps that the keyword classifier can't handle.

Called when the keyword classifier returns 'Likely Supportable' with no matches --
uses an LLM to analyze scraped content + Trino context and produce a policy verdict.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_POLICY_EXCERPT: Optional[str] = None


def _load_policy_excerpt() -> str:
    global _POLICY_EXCERPT
    if _POLICY_EXCERPT is not None:
        return _POLICY_EXCERPT
    for name in ("policy/policy-excerpt.txt",
                 "Copy of [Wix] Stripe Supportability Handling Guide_Jan25 (2).docx"):
        p = PROJECT_ROOT / name
        if p.exists():
            if p.suffix == ".docx":
                try:
                    from docx import Document
                    doc = Document(p)
                    _POLICY_EXCERPT = "\n".join(
                        para.text.strip() for para in doc.paragraphs if para.text.strip()
                    )
                    return _POLICY_EXCERPT
                except Exception:
                    continue
            else:
                _POLICY_EXCERPT = p.read_text(encoding="utf-8", errors="replace")[:8000]
                return _POLICY_EXCERPT
    _POLICY_EXCERPT = ""
    return _POLICY_EXCERPT


def _call_llm(prompt: str, model: str = "gpt-4o-mini") -> Optional[str]:
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key)
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return (r.choices[0].message.content or "").strip()
        except Exception:
            pass
    # Ollama fallback
    import json
    from urllib.request import Request, urlopen
    try:
        body = json.dumps({
            "model": "llama3.1:8b",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode()
        req = Request("http://localhost:11434/api/chat", data=body,
                       headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return (data.get("message", {}).get("content") or "").strip()
    except Exception:
        return None


_QUICK_CLASSIFY_PROMPT = """You are a payment compliance analyst. Given the app information below, determine if this app complies with the Stripe/Wix payment policy.

**App name:** {app_name}
**App URL:** {app_url}
**App description:** {description}
**Builder conversation summary:** {conversation}
**Scraped page content (excerpt):** {content}

**Policy summary (key prohibited/restricted categories):**
- NOT allowed: Gambling/casino/betting, illegal drugs/cannabis/CBD, weapons/firearms, adult/porn content, debt collection/credit repair, counterfeit goods, get-rich-quick/MLM schemes, IPTV piracy, government form resellers, transaction laundering
- RESTRICTED (need review): Alcohol, tobacco/vape, pharmaceuticals/supplements, telehealth, crowdfunding/donations, insurance, marketplaces/multi-vendor, knives, content creator platforms, financial services, psychic/spiritual services
- NOT ENABLED for Wix: Cryptocurrency exchange/wallets, financial services (loans/BNPL/brokerage)

Based ONLY on the evidence above, respond with exactly one line in this format:
VERDICT: Allowed | Restricted | Not-allowed
CATEGORY: [the most relevant policy category, or "None"]
REASONING: [1-2 sentences explaining why]

If there is insufficient evidence to determine what the app sells, say:
VERDICT: Insufficient
CATEGORY: None
REASONING: [explain what is missing]
"""


def llm_classify(
    app_name: str = "",
    app_url: str = "",
    app_description: str = "",
    conversation_summary: str = "",
    scraped_content: str = "",
) -> Optional[dict]:
    """Quick LLM classification. Returns dict with verdict/category/reasoning or None if LLM unavailable."""
    if not any([app_description, conversation_summary, scraped_content]):
        return None

    prompt = _QUICK_CLASSIFY_PROMPT.format(
        app_name=app_name or "Unknown",
        app_url=app_url or "N/A",
        description=app_description or "Not provided",
        conversation=(conversation_summary or "Not available")[:3000],
        content=(scraped_content or "Not available")[:4000],
    )

    raw = _call_llm(prompt)
    if not raw or len(raw) < 20:
        return None

    return _parse_llm_response(raw)


def _parse_llm_response(raw: str) -> dict:
    result = {
        "llm_verdict": "Unknown",
        "llm_category": "",
        "llm_reasoning": "",
        "llm_raw": raw[:500],
    }
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("VERDICT:"):
            v = line.split(":", 1)[1].strip()
            for canonical in ("Not-allowed", "Restricted", "Insufficient", "Allowed"):
                if canonical.lower() in v.lower():
                    result["llm_verdict"] = canonical
                    break
        elif line.upper().startswith("CATEGORY:"):
            result["llm_category"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("REASONING:"):
            result["llm_reasoning"] = line.split(":", 1)[1].strip()
    return result


def map_llm_to_screener(llm_result: dict) -> tuple[str, str, int]:
    """Map LLM verdict to screener verdict, color, and confidence."""
    v = llm_result.get("llm_verdict", "Unknown")
    if v == "Not-allowed":
        return "Likely Not Supportable — Review", "red", 55
    elif v == "Restricted":
        return "Restricted — Review", "orange", 50
    elif v == "Insufficient":
        return "Needs Review", "orange", 30
    elif v == "Allowed":
        return "Likely Supportable", "green", 55
    return "Needs Review", "orange", 25
