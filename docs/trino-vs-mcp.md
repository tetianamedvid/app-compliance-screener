# Trino vs MCP — how to use Trino in the UW app

## What is Trino MCP?

**MCP (Model Context Protocol)** is a way to give an **AI assistant** (e.g. Cursor) access to tools. Your “Trino MCP” is a **server** that exposes tools like “run this SQL on Trino”. When you use Cursor and it runs a Trino query, Cursor talks to the MCP server, and the MCP server talks to Trino.

So:
- **MCP server** = middle process that has Trino credentials and runs queries.
- **Cursor** = MCP client that calls those tools when you ask it to analyze data.

## Do we “embed” MCP in the UW app?

**No.** The UW app is a normal app for humans (lookup by app_id/msid/WP id → profile + UW). It doesn’t need the AI in the loop, so it doesn’t need MCP.

What the UW app needs is to **run Trino queries itself** when someone does a lookup. So the app should talk to **Trino directly** (or via a small backend that has Trino), not via MCP.

## How the UW app gets Trino data

| Option | How it works | When to use |
|--------|----------------|-------------|
| **Trino Python client** | Your app (e.g. Streamlit/FastAPI on someone’s laptop) uses the `trino` library and connects to Trino with host/port/catalog/user (and auth if required). Each lookup = 1–2 queries. | Trino is reachable from the machine where the app runs (e.g. over VPN). |
| **Small backend with Trino** | A server (e.g. FastAPI) has Trino credentials and exposes endpoints like “resolve(id_type, value)” and “profile(app_id)”. The desktop app or browser calls this backend over HTTP. | Trino is only on a private network; only the server can reach it; laptops call the server. |
| **MCP** | Not used inside the UW app. MCP stays for Cursor/AI to run ad‑hoc Trino queries when you’re analyzing data in the repo. | You keep using Cursor + Trino MCP for ad‑hoc analysis; the UW app uses one of the two options above. |

So: **you don’t embed Trino MCP in the app.** You either embed the **Trino client** (option 1) or call a **backend that uses the Trino client** (option 2).

## Desktop/laptop app (no SSO)

If the app runs on people’s laptops (e.g. “run this script” or “open this local page”):

- **No SSO** — only people who have the app and (if needed) VPN can use it.
- **Trino:** Use the Trino client in the app (option 1), with connection settings in `.env` or a config file. If from laptops Trino isn’t reachable, run the same app (or a thin API) on a server that can reach Trino and have users open that URL (option 2).

**Done in this repo:** The UW app uses the `trino` package when `TRINO_HOST` is set (live Trino on each lookup). When not set, it uses the JSON stub. You can run the **desktop app** with `streamlit run uw_app/streamlit_app.py` on your laptop — no SSO, no API key; only people who run the app (e.g. over VPN) can use it.
