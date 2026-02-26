#!/usr/bin/env python3
"""
Build real_apps.json and full_profiles.json from Trino MCP query results.
Run after MCP queries; uses embedded data from this session's MCP responses.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# From MCP: WP apps with metadata (136 rows, deduped by app_id)
APPS_ROWS = [
    ["685b7fb87461017a0816baa3", "TGF Marketing", "https://tgf-marketing-0816baa3.base44.app", "6e65d85d-bdb5-4b26-945f-6ceb18dffc15", "3da00226-2f8d-4348-a445-3b1e41c08af8", "Your all-in-one platform to secure premium press coverage and elevate your brand.", "public_without_login", "[]"],
    ["6873fd66c02e10285e94132a", "Nate Hance's Song Request Show", "https://nate-hances-song-request-show-5e94132a.base44.app", "96cbf7e1-ea04-4fac-96c1-0ffd98982633", "8b612e80-31ac-4531-a03f-7dcc30cf3bfa", "Submit your song requests for Nate's live performances and see what's coming up next!", "public_without_login", "[]"],
    ["688bcae27cdd620719b414f7", "איה בעולם האותיות", "https://-19b414f7.base44.app", "a643c8d2-dbdc-4b3e-91c5-98365dbe7bba", "1676f252-127f-48fc-b42e-fb797a974456", "אפליקציה חינוכית וצבעונית ללימוד אותיות הא'-ב' בעברית, בהשראת ספרי 'זאביק קורא' האהובים.", "public_with_login", "[]"],
    ["6896232f6fe2438f6e61e302", "TurnMyBnb", "https://clean-sync-6e61e302.base44.app", "4de6846f-0186-4152-a8ef-51a1a1f5704c", "fcf90e17-6e66-4a64-a434-1c59f4360ec1", "TurnMyBnb is the ultimate platform for Airbnb hosts and professional cleaners...", "public_without_login", '["Business Tools", "Productivity", "Social & Community"]'],
    ["68af8225c7234d4fdac6846f", "OPNLABEL", "https://opnlabel-dac6846f.base44.app", "dca55c35-04cf-467e-af9b-562842771db8", "c4a7d373-4740-4e8d-a075-ceb6bff4284e", "The official coming soon page for OPNLABEL...", "public_without_login", '["Lifestyle & Hobbies", "Marketing & Sales"]'],
    ["68be9374f3c499a61b2347b4", "e-tinerary", "https://tourlicity2-1b2347b4.base44.app", "f4167423-f9b0-4d03-809b-31a9c8cd12ea", "49acfe02-3398-4287-b54e-e71eef50677b", "Tourlicity is a comprehensive platform that connects tour companies...", "public_with_login", "[]"],
    ["690e2a181b31cc9882f21543", "Holistic Wellness AI", "https://holistic-wellness-ai-82f21543.base44.app", "ff461fad-72f3-418f-94f6-63a352b9123a", "cacfb685-b0c7-4dc9-a4e9-77dab719d97a", "Your personal AI companion for achieving a balanced and healthy lifestyle...", "public_with_login", '["Lifestyle & Hobbies", "Community", "Data & Analytics"]'],
    ["690e443097d67b2c9dbd0bae", "The Candle Bar", "https://fragrance-box-9dbd0bae.base44.app", "236823f4-addf-4bd5-ac96-f637faa12f25", "6507802f-1557-49ef-b44b-50c4c427cf02", "Manage your perfume subscriptions, discover new scents...", "public_without_login", '["Lifestyle & Hobbies", "Operations"]'],
    ["6914aa3bd56f6388c0b30a84", "QUIKPICS", "https://bankrollbattle.base44.app", "f88eea62-859b-4fca-ac1c-b5af83face80", "49a33369-8227-481c-a34f-bc80e5785dd5", "Picks generated with 10 years of research and quant behind it", "public_without_login", '["Games & Entertainment", "Data & Analytics"]'],
    ["691c1df211eee0e6169c08ba", "NailScan Pro", "https://nail-scan-pro-169c08ba.base44.app", "bcd21bcf-2a1e-434f-afa3-a32f7e317890", "b238a9ac-8f8f-4e67-8c10-60c1baebc2e3", "NailScan Pro is your personal AR nail sizing assistant...", "public_without_login", "[]"],
    ["69547dfee8097958754fefbd", "Virtual CFPM - Food and Retail Solutions", "https://virtualcfpm.base44.app", "ffcbd582-39c5-47a1-b66e-66da68518964", "15ccd6ff-4568-4690-96d7-9357bc2be06c", "Your all-in-one platform for professional food safety education...", "public_without_login", '["Education", "Operations"]'],
    ["695b92beeea3f03c3c489ac0", "Contract Guard AI", "https://contract-guard-ai-3c489ac0.base44.app", "58462fd2-9897-4173-aeb0-d1bd63cd08b3", "b86d7880-f17a-4b61-87a9-3926e0f0d76b", "Upload your contracts and let AI identify potential risks...", "public_with_login", '["Operations", "Data & Analytics", "HR & Legal"]'],
    ["6963b7f53dbd52799186bb6c", "Brainrot Gacha", "https://brainrot-gacha-9186bb6c.base44.app", "62855fb2-12fc-40be-af7f-4ff76871b51b", "b27acbf3-6172-42cf-95ec-338b92a3656b", "Roll for your daily dose of internet culture and meme magic...", "public_with_login", '["Games & Entertainment", "Lifestyle & Hobbies"]'],
    ["6969398185102ec71490bb1e", "BEHINDSTICKER Motorsport", "https://behindsticker.base44.app", "b6a47836-76cc-4c5d-9139-2b79de1ff4b9", "606772ba-47a1-4607-9048-e9793575fd2a", "A professional dealership web app for managing inventory...", "public_without_login", '["Marketing & Sales", "Operations"]'],
    ["696d138ec2a058d0e7ff9b90", "SecondChance", "https://second-chance-e7ff9b90.base44.app", "73e5749a-c400-4beb-a2b5-cacd633a2a34", "489103e3-18d2-4471-b2b2-623946514a9c", "Your go-to marketplace for buying and selling items...", "public_with_login", '["Operations", "Community", "Lifestyle & Hobbies"]'],
    ["69705277a29f49c31686da30", "Fruit Shooter", "https://fruit-frenzy-1686da30.base44.app", "92612c0c-5c80-4a99-8198-f32b18031bc4", "7a42a8f0-5286-4ac7-aa49-58a33ec1b78b", "A fast-paced arcade game where you shoot fruits...", "public_without_login", '["Games & Entertainment", "Lifestyle & Hobbies"]'],
    ["6973f4d46427a756b6cd2488", "ClientHub", "https://interstateclienthub.base44.app", "fea87c16-93be-4f1b-af3b-a351b4b69ffc", "e6c2efcf-c81c-426b-b8bd-f1093394b2e7", "A comprehensive customer portal for managing account information...", "public_with_login", '["Operations", "Finance"]'],
]

COLS = ["app_id", "app_name", "app_url", "msid", "account_id", "user_description", "public_settings", "categories"]

# User logs from MCP (80 rows) - key fields
USER_LOGS = {
    "685b7fb87461017a0816baa3": (7, "2025-10-15 14:34:18.614000 UTC", "2025-10-15 14:35:49.688000 UTC"),
    "6896232f6fe2438f6e61e302": (6011, "2025-09-28 16:45:22.751000 UTC", "2026-02-24 06:48:18.713000 UTC"),
    "68be9374f3c499a61b2347b4": (871, "2025-09-27 13:58:10.928000 UTC", "2025-10-28 20:00:14.074000 UTC"),
    "6914aa3bd56f6388c0b30a84": (94, "2026-02-22 16:07:39.642000 UTC", "2026-02-24 01:08:00.058000 UTC"),
    "6969398185102ec71490bb1e": (209, "2026-02-22 15:45:46.557000 UTC", "2026-02-24 06:00:48.923000 UTC"),
    "69705277a29f49c31686da30": (81, "2026-02-22 18:37:15.451000 UTC", "2026-02-24 06:02:59.139000 UTC"),
    "698406273ade17b9bd851188": (47, "2026-02-22 22:57:45.051000 UTC", "2026-02-24 03:02:12.293000 UTC"),
    "698e9adbbe8b990f1f47f603": (240, "2026-02-22 15:47:25.514000 UTC", "2026-02-24 06:48:24.005000 UTC"),
    "69948763ca2ad1934bdd9654": (112, "2026-02-22 17:36:30.777000 UTC", "2026-02-22 23:12:55.240000 UTC"),
    "699502070dc4dc275deb351f": (1175, "2026-02-22 15:37:47.724000 UTC", "2026-02-24 01:54:01.962000 UTC"),
    "69990440a6ff02254b9a9862": (22, "2026-02-22 20:56:14.582000 UTC", "2026-02-23 20:26:56.386000 UTC"),
    "6999400b6985907082a94b33": (211, "2026-02-22 23:55:00.290000 UTC", "2026-02-23 08:07:56.660000 UTC"),
}


def _ts(s):
    if not s:
        return None
    return str(s)[:19].replace("T", " ").replace(" UTC", "")


def main():
    DATA.mkdir(parents=True, exist_ok=True)

    # Build app list: merge all apps from d4e23aa5 file + APPS_ROWS (prefer rows with metadata)
    apps_by_id = {}
    for row in APPS_ROWS:
        d = dict(zip(COLS, row))
        aid = (d.get("app_id") or "").strip()
        if not aid:
            continue
        apps_by_id[aid] = {
            "app_id": aid,
            "app_name": d.get("app_name") or "",
            "app_url": d.get("app_url") or "",
            "msid": d.get("msid") or "",
            "account_id": d.get("account_id") or "",
            "user_description": d.get("user_description") or "",
            "public_settings": d.get("public_settings") or "",
            "categories": d.get("categories") or "[]",
        }

    # Load existing real_apps to get full list (99 apps), merge metadata
    real_path = DATA / "real_apps.json"
    if real_path.exists():
        try:
            existing = json.loads(real_path.read_text(encoding="utf-8"))
            for r in existing:
                aid = (r.get("app_id") or "").strip()
                if not aid:
                    continue
                if aid not in apps_by_id:
                    apps_by_id[aid] = {
                        "app_id": aid,
                        "app_name": r.get("app_name") or "",
                        "app_url": r.get("app_url") or "",
                        "msid": r.get("msid") or "",
                        "account_id": r.get("account_id") or "",
                        "user_description": r.get("user_description") or "",
                        "public_settings": r.get("public_settings") or "",
                        "categories": r.get("categories") or "[]",
                    }
                else:
                    # Merge: prefer existing metadata if we have empty
                    for k in ("user_description", "public_settings", "categories"):
                        if not (apps_by_id[aid].get(k) or "").strip() and (r.get(k) or "").strip():
                            apps_by_id[aid][k] = r.get(k) or ""
        except Exception:
            pass

    apps_list = list(apps_by_id.values())
    real_path.write_text(json.dumps(apps_list, indent=2), encoding="utf-8")
    print("Wrote", real_path, "with", len(apps_list), "apps.")

    # Build full_profiles from apps + user_logs + existing trino_conversations
    full = {}
    for app in apps_list:
        aid = (app.get("app_id") or "").strip()
        if not aid:
            continue
        full[aid] = {
            "user_description": app.get("user_description") or "",
            "public_settings": app.get("public_settings") or "",
            "categories": app.get("categories") or "[]",
        }
        ul = USER_LOGS.get(aid)
        if ul:
            full[aid]["user_app_events_count"] = ul[0]
            full[aid]["first_activity_at"] = _ts(ul[1])
            full[aid]["user_apps_last_activity_at"] = _ts(ul[2])

    # Merge from existing trino files
    for name, key in [("trino_conversations.json", "conversation_snapshots"), ("trino_earliest_conversation_preview.json", "earliest")]:
        p = DATA / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            rows = data.get("rows") or []
            cols = data.get("col_names") or []
            for row in rows:
                d = dict(zip(cols, row))
                aid = (d.get("app_id") or "").strip()
                if not aid or aid not in full:
                    continue
                if key == "conversation_snapshots":
                    content = (d.get("conversation_summary") or "").strip()
                    if content:
                        full[aid].setdefault("conversation_snapshots", []).append({
                            "created_at": _ts(d.get("updated_date")) or "—",
                            "content": content[:50000],
                        })
                elif key == "earliest":
                    preview = (d.get("earliest_conversation_preview") or "").strip()
                    if preview:
                        full[aid]["earliest_conversation_preview"] = preview
                        full[aid]["earliest_conversation_first_at"] = _ts(d.get("earliest_conversation_first_at"))
        except Exception as e:
            print("Warn:", name, e)

    fp_path = DATA / "full_profiles.json"
    fp_path.write_text(json.dumps(full, indent=2), encoding="utf-8")
    print("Wrote", fp_path, "with", len(full), "apps.")
    print("Done. Restart dashboard: streamlit run streamlit_uw.py")


if __name__ == "__main__":
    main()
