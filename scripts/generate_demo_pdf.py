"""Generate a visual demo PDF with screenshots and captions."""
from pathlib import Path
from fpdf import FPDF

DOCS = Path(__file__).resolve().parent.parent / "docs"
SHOTS = DOCS / "demo-screenshots"
OUT = DOCS / "DEMO-WALKTHROUGH.pdf"

STEPS = [
    ("step1-home-screen.png",
     "Step 1 - Open the App",
     "Open the screener in your browser. You see the title, URL input box, "
     "Deep scrape toggle (on by default), and the Screen button."),
    ("step2-url-entered.png",
     "Step 2 - Enter a URL",
     "Paste a URL into the text box (e.g. https://imperialcasino.base44.app). "
     "You can enter multiple URLs, one per line."),
    ("step3-verdict-result.png",
     "Step 3 - View the Verdict",
     "After clicking Screen, the verdict appears: app name, URL, "
     "Not Supportable / Restricted / Likely Supportable badge, confidence "
     "score, and matched policy category with details."),
    ("step4-findings-table.png",
     "Step 4 - Findings Table and KPIs",
     "Scroll down to the Findings Table. The KPI bar shows totals: "
     "391 Total Screened, 14 Not Supportable, etc. "
     "The table lists all screened apps with columns for URL, Name, "
     "Verdict, Confidence, and Review Status."),
    ("step5-kpi-bar.png",
     "Step 5 - Filter and Search",
     "Use the Verdict and Review Status dropdowns to filter. "
     "Use the Search box to find specific apps by name or URL. "
     "The table shows 'Showing X of Y' to confirm your filter."),
    ("step6-view-content-dropdown.png",
     "Step 6 - Select App for Page Content",
     "Below the table, use the dropdown to select an app. "
     "Then click 'View content' to see what the scraper found."),
    ("step7-content-popup.png",
     "Step 7 - Page Content Popup",
     "A popup window shows the app name, verdict badge, URL, "
     "number of characters scraped, and the full scraped page text "
     "(metadata, config, entities, page text). "
     "Close the popup by clicking X or clicking outside it."),
]


class DemoPDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, "App Compliance Screener - Visual Demo", align="C")
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def build():
    pdf = DemoPDF(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    # Title page
    pdf.add_page()
    pdf.ln(60)
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 14, "App Compliance Screener", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 16)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 10, "Visual Demo Walkthrough", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, "Paste any app URL -> instant scrape + policy classification -> verdict",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(30)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 8, "https://app-compliance-screener-tihxaacrkagcy2ijutdvsk.streamlit.app",
             align="C", new_x="LMARGIN", new_y="NEXT")

    pw = 170  # usable page width in mm

    for fname, title, caption in STEPS:
        img_path = SHOTS / fname
        if not img_path.exists():
            continue

        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(60, 60, 60)
        pdf.multi_cell(0, 5, caption)
        pdf.ln(4)

        img_w_px, img_h_px = 0, 0
        from PIL import Image
        with Image.open(img_path) as im:
            img_w_px, img_h_px = im.size

        aspect = img_h_px / img_w_px
        img_w_mm = min(pw, 120)
        img_h_mm = img_w_mm * aspect
        max_h = 200
        if img_h_mm > max_h:
            img_h_mm = max_h
            img_w_mm = img_h_mm / aspect

        x_offset = (210 - img_w_mm) / 2
        pdf.image(str(img_path), x=x_offset, w=img_w_mm)

    pdf.output(str(OUT))
    print(f"Created: {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    build()
