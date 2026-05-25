"""Build the reproducible demo-corpus binary files (PDF + xlsx).

Theme: NorthWind Capital evaluates Vertex AI Platform. Cross-references
the .txt amendment, .md eval notes, and .eml sales thread in the same
directory so entity resolution + doc-chain detection + conflict
cascade have ammunition.

Usage:
    uv run --with reportlab --with xlsxwriter python demo-corpus/build.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)
import xlsxwriter


ROOT = Path(__file__).parent


# ---------------------------------------------------------------------------
# vertex-msa.pdf  — the original Master Services Agreement
# ---------------------------------------------------------------------------


MSA_PARAS = [
    ("Title", "MASTER SERVICES AGREEMENT"),
    ("Body",
     "This Master Services Agreement (the &quot;Agreement&quot;) is entered "
     "into as of January 15, 2026 (the &quot;Effective Date&quot;) by and "
     "between NorthWind Capital LLC, a Delaware limited liability company "
     "(&quot;NorthWind&quot;), and Vertex Industries Ltd., a Maharashtra "
     "company (&quot;Vertex&quot;)."),

    ("H2", "1. SCOPE OF SERVICES"),
    ("Body",
     "Vertex shall provide document-intelligence services to NorthWind as "
     "more specifically described in Schedule A. Services shall be performed "
     "at Vertex facilities in Mumbai and Pune. The Aurangabad facility is "
     "not in scope under this Agreement; the parties may add it via written "
     "amendment."),

    ("H2", "2. TERM"),
    ("Body",
     "The initial term of this Agreement shall be three (3) years from the "
     "Effective Date, automatically renewing for successive one-year terms "
     "unless either party provides written notice of non-renewal at least "
     "ninety (90) days prior to the expiration of the then-current term."),

    ("H2", "3. PAYMENT TERMS"),
    ("Body",
     "NorthWind shall pay Vertex on a net-thirty (30) day basis upon "
     "receipt of invoice. Late payments shall accrue interest at the rate "
     "of one and one-half percent (1.5%) per month."),

    ("H2", "4. INDEMNIFICATION"),
    ("Body",
     "Vertex shall indemnify and hold harmless NorthWind from any claims "
     "arising out of Vertex's gross negligence or willful misconduct, "
     "provided that Vertex's aggregate liability under this Section 4 "
     "shall not exceed twenty-five million dollars ($25,000,000) per "
     "occurrence."),

    ("H2", "5. TERMINATION"),
    ("Body",
     "Either party may terminate this Agreement for material breach upon "
     "sixty (60) days' written notice to the breaching party, provided the "
     "breach has not been cured within such notice period."),

    ("H2", "6. CONFIDENTIALITY"),
    ("Body",
     "The parties acknowledge that they will exchange Confidential "
     "Information in the course of performing this Agreement. Each party "
     "shall protect such Confidential Information using the same degree of "
     "care it uses to protect its own confidential information, but in no "
     "event less than reasonable care, for a period of five (5) years from "
     "the date of disclosure."),

    ("H2", "7. GOVERNING LAW"),
    ("Body",
     "This Agreement shall be governed by and construed in accordance with "
     "the laws of the State of Delaware, without regard to its conflict of "
     "laws principles. Any dispute arising hereunder shall be resolved by "
     "binding arbitration in Wilmington, Delaware, under the Commercial "
     "Arbitration Rules of the American Arbitration Association."),

    ("H2", "8. SIGNATURES"),
    ("Body",
     "IN WITNESS WHEREOF, the parties have executed this Agreement as of "
     "the Effective Date."),
    ("Body",
     "<b>NORTHWIND CAPITAL LLC</b><br/>By: Sarah Chen, CFO<br/>"
     "Dated: January 15, 2026"),
    ("Body",
     "<b>VERTEX INDUSTRIES LTD.</b><br/>By: Rajesh Sharma, CEO<br/>"
     "Dated: January 15, 2026"),
]


def build_pdf() -> Path:
    out = ROOT / "vertex-msa.pdf"
    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.8 * inch, bottomMargin=0.8 * inch,
        title="Vertex MSA", author="NorthWind Capital LLC",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="MSATitle", parent=styles["Title"],
        fontSize=14, spaceAfter=18, alignment=1,
    ))
    styles.add(ParagraphStyle(
        name="MSAH2", parent=styles["Heading2"],
        fontSize=11, spaceBefore=12, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="MSABody", parent=styles["BodyText"],
        fontSize=10, leading=14, spaceAfter=6,
    ))

    story = []
    for kind, text in MSA_PARAS:
        if kind == "Title":
            story.append(Paragraph(text, styles["MSATitle"]))
        elif kind == "H2":
            story.append(Paragraph(text, styles["MSAH2"]))
        else:
            story.append(Paragraph(text, styles["MSABody"]))
            story.append(Spacer(1, 0.04 * inch))
    doc.build(story)
    return out


# ---------------------------------------------------------------------------
# vertex-pricing-tiers.xlsx
# ---------------------------------------------------------------------------


PRICING_HEADERS = [
    "Tier", "Min docs / month", "Max docs / month",
    "Per-doc rate (USD)", "Minimum monthly commit (USD)",
    "SLA (uptime)", "Region",
]


PRICING_ROWS = [
    ("Starter",      0,      10_000,  0.030, 300,    "99.5%", "Mumbai only"),
    ("Growth",       10_000, 50_000,  0.025, 1_250,  "99.7%", "Mumbai + Pune"),
    ("Business",     50_000, 150_000, 0.022, 3_300,  "99.9%", "Mumbai + Pune"),
    ("Enterprise",   150_000, 300_000, 0.018, 4_320, "99.95%", "Mumbai + Pune + Aurangabad"),
    ("Enterprise+",  300_000, None,    0.015, 6_000, "99.99%", "All sites + DR"),
]


def build_xlsx() -> Path:
    out = ROOT / "vertex-pricing-tiers.xlsx"
    book = xlsxwriter.Workbook(str(out))
    header_fmt = book.add_format({
        "bold": True, "bg_color": "#f4f4f5", "border": 1,
        "align": "left", "valign": "vcenter",
    })
    body_fmt = book.add_format({"border": 1, "valign": "vcenter"})
    money_fmt = book.add_format({"border": 1, "num_format": '"$"#,##0.00'})
    int_fmt = book.add_format({"border": 1, "num_format": "#,##0"})

    sheet = book.add_worksheet("Tiers")
    sheet.set_column(0, 0, 14)
    sheet.set_column(1, 2, 18)
    sheet.set_column(3, 4, 24)
    sheet.set_column(5, 5, 14)
    sheet.set_column(6, 6, 30)

    for col, name in enumerate(PRICING_HEADERS):
        sheet.write(0, col, name, header_fmt)

    for r, row in enumerate(PRICING_ROWS, start=1):
        tier, lo, hi, rate, commit, sla, region = row
        sheet.write(r, 0, tier, body_fmt)
        sheet.write(r, 1, lo, int_fmt)
        if hi is None:
            sheet.write(r, 2, "no cap", body_fmt)
        else:
            sheet.write(r, 2, hi, int_fmt)
        sheet.write(r, 3, rate, money_fmt)
        sheet.write(r, 4, commit, money_fmt)
        sheet.write(r, 5, sla, body_fmt)
        sheet.write(r, 6, region, body_fmt)

    # A second sheet with the NorthWind-specific committed scenario so the
    # eval harness has something concrete to aggregate over.
    s2 = book.add_worksheet("NorthWind Commit")
    s2.set_column(0, 0, 28)
    s2.set_column(1, 1, 22)

    rows = [
        ("Tier selected",            "Enterprise"),
        ("Committed sites",          "Mumbai + Pune + Aurangabad"),
        ("Projected monthly docs",   240_000),
        ("Per-doc rate (USD)",       0.018),
        ("Computed monthly (USD)",   240_000 * 0.018),
        ("Annual commit (USD)",      240_000 * 0.018 * 12),
        ("Indemnification cap (USD)", 50_000_000),
        ("Payment terms",            "net-45"),
        ("Effective from",           "April 1, 2026"),
    ]
    for r, (k, v) in enumerate(rows):
        s2.write(r, 0, k, header_fmt)
        if isinstance(v, (int, float)) and "rate" not in k.lower():
            s2.write(r, 1, v, int_fmt)
        elif isinstance(v, float):
            s2.write(r, 1, v, money_fmt)
        else:
            s2.write(r, 1, v, body_fmt)

    book.close()
    return out


if __name__ == "__main__":
    pdf = build_pdf()
    xlsx = build_xlsx()
    print(f"wrote {pdf}")
    print(f"wrote {xlsx}")
