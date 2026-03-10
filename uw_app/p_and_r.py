"""
P&R Index (Prohibited and Restricted) — main hierarchy for policy classification.

Loads:
- data/p_and_r_index.json — P&R categories (id, name, sensitivity, etc.)
- data/classifier_rule_to_p_and_r.json — mapping from Stripe (category, subcategory) → P&R IDs
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

_p_and_r_by_id: dict[int, dict] = {}
_rule_to_pr: dict[tuple[str, str], list[int]] = {}


def _load_p_and_r_index() -> dict[int, dict]:
    global _p_and_r_by_id
    if _p_and_r_by_id:
        return _p_and_r_by_id
    p = DATA / "p_and_r_index.json"
    if not p.exists():
        return {}
    import json
    with open(p) as f:
        items = json.load(f)
    for item in items:
        pid = item.get("p_and_r_id")
        if pid is not None:
            _p_and_r_by_id[int(pid)] = item
    return _p_and_r_by_id


def _load_rule_to_pr() -> dict[tuple[str, str], list[int]]:
    global _rule_to_pr
    if _rule_to_pr:
        return _rule_to_pr
    p = DATA / "classifier_rule_to_p_and_r.json"
    if not p.exists():
        return {}
    import json
    with open(p) as f:
        items = json.load(f)
    for item in items:
        c = item.get("stripe_category", "")
        s = item.get("stripe_subcategory", "")
        ids = item.get("p_and_r_ids", [])
        if c and ids:
            _rule_to_pr[(c, s)] = [int(x) for x in ids]
    return _rule_to_pr


def get_p_and_r_ids(category: str, subcategory: str) -> list[int]:
    """Get P&R IDs for a Stripe (category, subcategory) rule."""
    m = _load_rule_to_pr()
    return m.get((category, subcategory), [])


def get_p_and_r_name(p_and_r_id: int) -> str:
    """Get P&R category name by ID."""
    idx = _load_p_and_r_index()
    item = idx.get(p_and_r_id)
    return item.get("p_and_r_name", f"P&R #{p_and_r_id}") if item else f"P&R #{p_and_r_id}"


def get_p_and_r_for_rule(category: str, subcategory: str) -> list[tuple[int, str]]:
    """Get P&R (id, name) pairs for a given Stripe rule."""
    ids = get_p_and_r_ids(category, subcategory)
    return [(pid, get_p_and_r_name(pid)) for pid in ids]


def get_primary_p_and_r(category: str, subcategory: str) -> Optional[tuple[int, str]]:
    """Get primary (first) P&R for a rule. Returns (id, name) or None."""
    pairs = get_p_and_r_for_rule(category, subcategory)
    return pairs[0] if pairs else None
