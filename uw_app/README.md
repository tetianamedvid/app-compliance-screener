# UW Internal App — step-by-step for everyone

The UW app lets you look up an app by **App ID**, **MSID**, or **WixPayments account ID** and see its **app profile** and **UW check vs policy** (verdict, reasoning, what is sold, policy comparison).

**Full step-by-step guide (for non-devs):** See **docs/RUN-UW-APP-ON-MY-COMPUTER.md**. It explains every step in order, what to type, and what you will see.

---

## Quick steps: run the app on your computer

1. **Open Terminal** (Mac: search “Terminal”; Windows: Command Prompt or PowerShell).
2. **Go to the project folder:** type `cd "/path/to/Base44 analysis"` (use your real path) and press Enter.
3. **Install once:** type `pip3 install -r requirements.txt` and press Enter. Wait until it finishes.
4. **Start the app:** type `python3 -m streamlit run uw_app/streamlit_app.py` and press Enter.
5. **Use the browser** that opens (or go to http://localhost:8501). Choose “I have” (App ID / MSID / WP account ID), paste the value, click **Look up**.

**What you see:** A table (app profile) and the UW check (verdict, reasoning, expandable sections). By default only sample apps work until you set up live Trino.

---

## Quick steps: use live Trino (look up any app)

1. **Copy the config:** In the project folder, type `cp .env.example .env` and press Enter.
2. **Edit `.env`:** Open the `.env` file in a text editor. Set `TRINO_HOST=` to your Trino host (get it from your team or internal docs). Save.
3. **Connect to VPN** (and SSO if your org requires it).
4. **Start the app** (same as step 4 above). The page should say **“Using live data from Trino”**.
5. **Look up** any app_id, msid, or WixPayments account ID.

**Optional check:** Run `python3 scripts/test_live_trino.py` to test the Trino connection without opening the app. You should see “Live Trino test passed.”

---

## For developers (API)

If you want to call the app as an API instead of using the browser:

1. Start the API: `uvicorn uw_app.main:app --reload --port 8000`.
2. **POST /lookup** with body `{ "identifier_type": "app_id"|"msid"|"wp_account_id", "value": "..." }` returns app_id, app_name, app_profile, uw (verdict, reasoning, etc.).
3. **GET /uw/{app_id}** returns the cached UW result. **GET /health** returns status and whether Trino is live or stub.
