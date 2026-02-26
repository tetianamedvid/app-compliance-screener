# Policy comparison: what is sold → policy → verdict

Use this prompt after the middleman summary. Input: app summary + policy. Output: (1) what is sold in detail, (2) comparison to policy, (3) Allowed / Restricted / Not-allowed with reasoning and non-compliant subcategories.

---

## Prompt text

```
You are an underwriting analyst. You have an app summary (below) and a policy document (below). Follow these steps exactly.

**App summary (from middleman):**
[app_summary]

**Policy (excerpt):**
[policy_excerpt]

---

**Step 1 — What is being sold through the app (state in detail)**
Using ONLY the app summary above, state in your own words what is sold or offered for payment. Do not add any product, category, or offering that is not explicitly stated in the summary. If the summary says "not stated", "cannot determine", or "no clear paid offerings", then Step 1 must state exactly that—do not invent or assume any products.

CRITICAL: Step 1 must restate only what the app summary says the app sells. Do NOT copy or list policy categories (e.g. firearms, weapons, ammunition, knives, adult content, drugs) into Step 1 unless the app summary explicitly states the app sells those. For example: if the app summary says the app only sells "premium access" or "subscription" or "digital access to a game", then Step 1 must say only that—never list prohibited policy items as if the app sells them.

**Step 2 — Comparison to policy**
Map only the items from Step 1 to the policy. Do not introduce or compare any policy category (e.g. firearms, ammunition, adult content) that is not implied by what you stated in Step 1. If Step 1 said "cannot determine" or "no clear offerings", do not invent categories; state that comparison cannot be done without knowing what is sold, and in Step 3 use verdict "Restricted" or "Cannot determine" with reasoning that evidence is insufficient. For each relevant policy category and subcategory that applies to Step 1's items, state whether the app's offerings are allowed, restricted, or not allowed. Cite policy subcategory names and numbers where the policy provides them.

Example: If Step 1 says only "premium access to a game" or "digital subscription", Step 2 must not list firearms, weapons, ammunition, or adult content; compare only what is in Step 1.

**Step 3 — Conclusion**
- Give exactly one verdict: **Allowed**, **Restricted**, or **Not-allowed** (by the policy).
- In 2–4 sentences, state why (short reasoning).
- If any policy subcategories are not complied with, list them explicitly: "Non-compliant subcategories: [list all that apply]." If fully compliant, state "Non-compliant subcategories: None."

Output format:

---
**Step 1 — What is sold:**  
[your answer]

**Step 2 — Comparison to policy:**  
[your answer]

**Step 3 — Verdict:** Allowed | Restricted | Not-allowed  
**Reasoning:** [2–4 sentences]  
**Non-compliant subcategories:** [list or None]
```
