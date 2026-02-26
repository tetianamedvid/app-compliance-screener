#!/usr/bin/env python3
"""
Write data/trino_app_metadata.json from MCP Trino query result.
Run from project root. Uses embedded MCP response data (user_description, public_settings, categories).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT_PATH = DATA / "trino_app_metadata.json"

# From MCP: SELECT app_id, user_description, public_settings, categories
# for WP apps from base44_user_generated_apps_v2_mongo (16 with content)
MCP_METADATA = {
    "685b7fb87461017a0816baa3": ("Your all-in-one platform to secure premium press coverage and elevate your brand.", "public_without_login", "[]"),
    "688bcae27cdd620719b414f7": ("אפליקציה חינוכית וצבעונית ללימוד אותיות הא'-ב' בעברית, בהשראת ספרי 'זאביק קורא' האהובים.", "public_with_login", ""),
    "6896232f6fe2438f6e61e302": ("TurnMyBnb is the ultimate platform for Airbnb hosts and professional cleaners, offering an elegant, user-friendly solution to manage properties' cleaning schedules. Experience seamless coordination, efficient task management, and a visually appealing interface that simplifies property management.", "public_without_login", '["Business Tools", "Productivity", "Social & Community"]'),
    "68af8225c7234d4fdac6846f": ("The official coming soon page for OPNLABEL, a premium streetwear brand specializing in designed sweatsets. Get ready for a fusion of comfort, culture, and bold style.", "public_without_login", '["Lifestyle & Hobbies", "Marketing & Sales"]'),
    "68be9374f3c499a61b2347b4": ("Tourlicity is a comprehensive platform that connects tour companies and event planners with their customers. It provides a structured system for managing complex tours and events from planning to execution. The platform enables providers to create custom tours and events from standardized or blank templates and allows customers to access detailed itineraries, upload documents, and collaborate throughout their journey.", "public_with_login", "[]"),
    "690e2a181b31cc9882f21543": ("Your personal AI companion for achieving a balanced and healthy lifestyle. Holistic Wellness AI provides personalized insights, guided practices, and resources to nurture your mind, body, and spirit.", "public_with_login", '["Lifestyle & Hobbies", "Community", "Data & Analytics"]'),
    "690e443097d67b2c9dbd0bae": ("Manage your perfume subscriptions, discover new scents, and set your preferences.", "public_without_login", '["Lifestyle & Hobbies", "Operations"]'),
    "6914aa3bd56f6388c0b30a84": ("Picks generated with 10 years of research and quant behind it", "public_without_login", '["Games & Entertainment", "Data & Analytics"]'),
    "691c1df211eee0e6169c08ba": ("NailScan Pro is your personal AR nail sizing assistant. Get precise millimeter measurements of your natural nails using your smartphone's camera, ensuring a perfect fit for any custom press-on nails, every time.", "public_without_login", "[]"),
    "69547dfee8097958754fefbd": ("Your all-in-one platform for professional food safety education. Purchase courses, learn at your own pace with interactive content, schedule your virtual exams, and earn your certifications.", "public_without_login", '["Education", "Operations"]'),
    "695b92beeea3f03c3c489ac0": ("Upload your contracts and let AI identify potential risks and explain them in plain terms. Understand 'this could hurt you if.....', worst-case scenarios, and exit risks so you can make informed decisions.", "public_with_login", '["Operations", "Data & Analytics", "HR & Legal"]'),
    "6963b7f53dbd52799186bb6c": ("Roll for your daily dose of internet culture and meme magic. Discover and collect a variety of brainrot with different rarities, from common chuckles to legendary meme moments.", "public_with_login", '["Games & Entertainment", "Lifestyle & Hobbies"]'),
    "6969398185102ec71490bb1e": ("A professional dealership web app for managing inventory, financing, and customer leads with a luxury minimal interface.", "public_without_login", '["Marketing & Sales", "Operations"]'),
    "696d138ec2a058d0e7ff9b90": ("Your go-to marketplace for buying and selling items locally and nationwide. Discover great deals, ship with ease, and get paid securely.", "public_with_login", '["Operations", "Community", "Lifestyle & Hobbies"]'),
    "69705277a29f49c31686da30": ("A fast-paced arcade game where you shoot fruits, earn coins, upgrade weapons, and survive increasingly challenging waves.", "public_without_login", '["Games & Entertainment", "Lifestyle & Hobbies"]'),
    "6973f4d46427a756b6cd2488": ("A comprehensive customer portal for managing account information, viewing service tickets, work orders, quotes, and processing payments.", "public_with_login", '["Operations", "Finance"]'),
}

def main():
    col_names = ["app_id", "user_description", "public_settings", "categories"]
    rows = [[aid, ud, ps, cat] for aid, (ud, ps, cat) in MCP_METADATA.items()]
    out = {"rows": rows, "col_names": col_names}
    DATA.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("Wrote", OUT_PATH, "with", len(rows), "apps (metadata from MCP).")

if __name__ == "__main__":
    main()
