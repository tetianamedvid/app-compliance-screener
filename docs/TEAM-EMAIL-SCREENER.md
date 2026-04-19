# Team Email — App Compliance Screener

Copy everything below the line and paste into your email.

---

**Subject:** App Compliance Screener is live — check any app URL in seconds

Hi team,

The App Compliance Screener is now available as a web app. No install, no setup — just open the link and start screening.

**Open the screener:** [app-compliance-screener-tihxaacrkagcy2ijutdvsk.streamlit.app](https://app-compliance-screener-tihxaacrkagcy2ijutdvsk.streamlit.app)

### How to use it

1. Open the link above in your browser (Chrome, Safari, or Firefox all work).
2. Paste one or more app URLs into the text box (one URL per line).
3. Click **Screen**. Wait 5–15 seconds per URL.
4. You will see the verdict (Not Supportable / Restricted / Likely Supportable), the matched policy category, and a confidence score.
5. Scroll down to the **Findings Table** to see all screened apps. Use the **Verdict** and **Review Status** filters to narrow results. Change the Review dropdown to mark apps as Reviewed, Approved, or Declined.
6. To see what the scraper found on a page, use the **dropdown below the table** to pick an app, then click **View content** — a popup shows the full scraped text.

### What it can do

- Screen any public app URL against the Stripe/Wix policy (categories, subcategories, P&R index).
- Deep scrape with a real browser to catch JavaScript-rendered content.
- Track all results in a persistent findings table with review workflow.

### What it cannot do

- It does not connect to Trino (no VPN on the cloud). Conversation summaries and app metadata from Trino are not available in this version.
- Findings reset when the app is redeployed. Export to CSV if you need to keep a snapshot.

### How it works under the hood

The screener was built using **Cursor AI** (an AI-powered IDE) and will be migrated to **Claude** as the primary AI engine. Key engineering:

- **Rule-based policy classifier** — 42 rules across 31 policy categories (Gambling, Adult Content, Weapons, Drugs, Financial Services, etc.), each with weighted keyword groups. When the scraper finds keywords on a page, the classifier calculates a confidence score based on keyword weight and frequency. Single-word keywords use word-boundary matching to avoid false positives (e.g. "rum" won't match inside "forum"). Every rule maps to the P&R Index (Prohibited & Restricted hierarchy from the Stripe Supportability Handling Guide), enriched with ~120 compliance signals from regulation source documents for traceability.
- **Two-layer scraping** — fast HTTP fetch first, then Playwright (headless Chromium browser) for JavaScript-heavy pages. Extracts products, payment signals, login methods, and entity types.
- **Hosted on Streamlit Community Cloud** — auto-deploys from GitHub on every code change. Python 3.12, no infrastructure to manage.

Questions? Reply to this email or message me directly.
