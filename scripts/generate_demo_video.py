"""Generate a demo video (MP4) from screenshots with title cards and captions."""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DOCS = Path(__file__).resolve().parent.parent / "docs"
SHOTS = DOCS / "demo-screenshots"
OUT = DOCS / "DEMO-VIDEO.mp4"

W, H = 1280, 720
FPS = 1
SECONDS_PER_SLIDE = 5
BG = (30, 30, 35)

STEPS = [
    (None,
     "App Compliance Screener",
     "Visual Demo Walkthrough"),
    ("step1-home-screen.png",
     "Step 1: Open the App",
     "Title, URL input, Deep scrape toggle, Screen button"),
    ("step2-url-entered.png",
     "Step 2: Enter a URL",
     "Paste URL (e.g. imperialcasino.base44.app) and click Screen"),
    ("step3-verdict-result.png",
     "Step 3: View the Verdict",
     "Not Supportable | 100% confidence | Gambling policy match"),
    ("step4-findings-table.png",
     "Step 4: Findings Table & KPIs",
     "391 Total Screened | 14 Not Supportable | Filters, search, review"),
    ("step5-kpi-bar.png",
     "Step 5: Filter & Search",
     "Verdict dropdown, Review Status, Search box"),
    ("step6-view-content-dropdown.png",
     "Step 6: Select App for Page Content",
     "Pick an app from the dropdown, click View Content"),
    ("step7-content-popup.png",
     "Step 7: Page Content Popup",
     "Full scraped text in a popup window. Close with X."),
    (None,
     "Try it now!",
     "app-compliance-screener-tihxaacrkagcy2ijutdvsk.streamlit.app"),
]


def pil_to_cv2(pil_img):
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def make_text_frame(title: str, subtitle: str) -> np.ndarray:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
        sub_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except Exception:
        title_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), title, font=title_font)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, H // 2 - 60), title, fill=(255, 255, 255), font=title_font)

    bbox2 = draw.textbbox((0, 0), subtitle, font=sub_font)
    sw = bbox2[2] - bbox2[0]
    draw.text(((W - sw) // 2, H // 2 + 20), subtitle, fill=(180, 180, 180), font=sub_font)

    return pil_to_cv2(img)


def make_screenshot_frame(img_path: str, title: str, subtitle: str) -> np.ndarray:
    frame = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(frame)

    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        sub_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except Exception:
        title_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()

    # Title bar at top
    draw.rectangle([(0, 0), (W, 70)], fill=(45, 45, 50))
    draw.text((20, 12), title, fill=(255, 255, 255), font=title_font)
    draw.text((20, 42), subtitle, fill=(160, 160, 170), font=sub_font)

    # Screenshot
    shot = Image.open(img_path)
    max_w, max_h = W - 40, H - 90
    ratio = min(max_w / shot.width, max_h / shot.height)
    new_w = int(shot.width * ratio)
    new_h = int(shot.height * ratio)
    shot = shot.resize((new_w, new_h), Image.LANCZOS)

    x_off = (W - new_w) // 2
    y_off = 75 + (max_h - new_h) // 2
    frame.paste(shot, (x_off, y_off))

    return pil_to_cv2(frame)


def build():
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(OUT), fourcc, FPS, (W, H))

    for img_file, title, subtitle in STEPS:
        if img_file is None:
            frame = make_text_frame(title, subtitle)
        else:
            path = SHOTS / img_file
            if not path.exists():
                continue
            frame = make_screenshot_frame(str(path), title, subtitle)

        for _ in range(SECONDS_PER_SLIDE * FPS):
            writer.write(frame)

    writer.release()
    size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"Created: {OUT}  ({size_mb:.1f} MB, {len(STEPS) * SECONDS_PER_SLIDE}s)")


if __name__ == "__main__":
    build()
