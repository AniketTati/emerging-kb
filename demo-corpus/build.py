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
        invariant=1,
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
    _freeze_workbook(book)
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


# ===========================================================================
# Broader corpus (PR3 / waveB) — 20 additional docs spanning 8 domains and
# 5 formats so extraction quality isn't optimized to a single persona.
#
# Shared theme: NorthWind / Vertex recur across many docs so entity
# resolution + doc-chain + cross-doc citation can be exercised. A subset
# uses unrelated fictional entities for breadth (resumes, lab results,
# bug reports, …).
# ===========================================================================


# ---------------------------------------------------------------------------
# Shared PDF helpers
# ---------------------------------------------------------------------------


def _pdf_styles():
    """Return a styles registry with our standard title/h2/body. Caller
    paragraphs with Paragraph(text, styles['Body']) etc."""
    s = getSampleStyleSheet()
    if "Title2" not in s.byName:
        s.add(ParagraphStyle(
            name="Title2", parent=s["Title"], fontSize=14,
            spaceAfter=14, alignment=1,
        ))
    if "H2_" not in s.byName:
        s.add(ParagraphStyle(
            name="H2_", parent=s["Heading2"],
            fontSize=11, spaceBefore=12, spaceAfter=6,
        ))
    if "Body_" not in s.byName:
        s.add(ParagraphStyle(
            name="Body_", parent=s["BodyText"],
            fontSize=10, leading=14, spaceAfter=6,
        ))
    if "Small" not in s.byName:
        s.add(ParagraphStyle(
            name="Small", parent=s["BodyText"],
            fontSize=9, leading=12, spaceAfter=4, textColor="#52525b",
        ))
    return s


def _render_pdf(out: Path, story: list, *, title: str, author: str = "KB demo") -> Path:
    # invariant=1 freezes the PDF /CreationDate, /ModDate, and document /ID
    # so re-running build.py with no source changes produces byte-identical
    # output → content-sha dedup catches the duplicate at upload time.
    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.8 * inch, bottomMargin=0.8 * inch,
        title=title, author=author,
        invariant=1,
    )
    doc.build(story)
    return out


def _freeze_workbook(book) -> None:
    """Pin the xlsx metadata + ZIP-entry timestamps so a re-run produces
    byte-identical bytes (content-sha dedup catches the duplicate at
    upload time). Without this xlsxwriter stamps each entry with the
    current time and the workbook's `created` property with `now()`."""
    import datetime as _dt
    book.set_properties({
        "created": _dt.datetime(2026, 1, 1, 0, 0, 0),
    })
    # xlsxwriter copies this attribute into every internal ZipFile entry's
    # `date_time` tuple at packaging time; freezing it eliminates the
    # last source of byte-level non-determinism.
    book.created = _dt.datetime(2026, 1, 1, 0, 0, 0)


def _para_from(kind_text_pairs, styles) -> list:
    story = []
    for kind, text in kind_text_pairs:
        if kind == "Title":
            story.append(Paragraph(text, styles["Title2"]))
        elif kind == "H2":
            story.append(Paragraph(text, styles["H2_"]))
        elif kind == "Small":
            story.append(Paragraph(text, styles["Small"]))
        else:
            story.append(Paragraph(text, styles["Body_"]))
            story.append(Spacer(1, 0.04 * inch))
    return story


# ---------------------------------------------------------------------------
# Doc 6 — mutual-nda.pdf  (legal)
# ---------------------------------------------------------------------------


_NDA = [
    ("Title", "MUTUAL NON-DISCLOSURE AGREEMENT"),
    ("Body",
     "This Mutual Non-Disclosure Agreement (this &quot;Agreement&quot;) is "
     "entered into as of February 20, 2026, by and between NorthWind Capital "
     "LLC, a Delaware limited liability company (&quot;NorthWind&quot;), and "
     "Helios Analytics Inc., a California corporation (&quot;Helios&quot;)."),
    ("H2", "1. CONFIDENTIAL INFORMATION"),
    ("Body",
     "&quot;Confidential Information&quot; means any non-public information "
     "disclosed by one party (the &quot;Discloser&quot;) to the other (the "
     "&quot;Recipient&quot;), whether oral, written, or electronic, including "
     "but not limited to business plans, financial data, customer lists, "
     "technical specifications, and source code."),
    ("H2", "2. TERM"),
    ("Body",
     "This Agreement shall remain in effect for a period of two (2) years "
     "from the Effective Date, unless earlier terminated by mutual written "
     "consent. Each Recipient's obligations of non-disclosure shall survive "
     "for an additional three (3) years after termination."),
    ("H2", "3. EXCLUSIONS"),
    ("Body",
     "Confidential Information does not include information that (a) is or "
     "becomes publicly available through no breach of this Agreement, (b) was "
     "in the Recipient's possession prior to disclosure, or (c) is "
     "independently developed by the Recipient without reference to the "
     "Discloser's Confidential Information."),
    ("H2", "4. GOVERNING LAW"),
    ("Body",
     "This Agreement shall be governed by the laws of the State of New York. "
     "Disputes shall be resolved by arbitration in New York, NY under the "
     "JAMS Streamlined Arbitration Rules."),
    ("H2", "5. SIGNATURES"),
    ("Body",
     "<b>NORTHWIND CAPITAL LLC</b><br/>By: Sarah Chen, CFO<br/>"
     "Dated: February 20, 2026"),
    ("Body",
     "<b>HELIOS ANALYTICS INC.</b><br/>By: Maria Gonzalez, General Counsel<br/>"
     "Dated: February 20, 2026"),
]


def build_nda_pdf() -> Path:
    return _render_pdf(
        ROOT / "mutual-nda.pdf",
        _para_from(_NDA, _pdf_styles()),
        title="Mutual NDA",
    )


# ---------------------------------------------------------------------------
# Doc 7 — saas-subscription-agreement.pdf  (legal / different customer)
# ---------------------------------------------------------------------------


_SAAS = [
    ("Title", "VERTEX AI PLATFORM SUBSCRIPTION AGREEMENT"),
    ("Body",
     "This Subscription Agreement (the &quot;Agreement&quot;) is entered into "
     "as of March 3, 2026 by and between Vertex Industries Ltd. "
     "(&quot;Vertex&quot;) and Pinnacle Holdings Pte. Ltd., a Singapore "
     "private limited company (&quot;Customer&quot;)."),
    ("H2", "1. SUBSCRIPTION"),
    ("Body",
     "Customer subscribes to the Business tier of the Vertex AI Platform for "
     "an initial term of twelve (12) months. The subscription includes up to "
     "100,000 documents per month, with overages billed at USD $0.025 per "
     "document. Auto-renews for successive 12-month terms."),
    ("H2", "2. FEES"),
    ("Body",
     "Subscription fee is USD $3,300 per month, billed quarterly in advance. "
     "Customer's first invoice is due on April 1, 2026."),
    ("H2", "3. SERVICE LEVELS"),
    ("Body",
     "Vertex commits to 99.9% monthly uptime. Failure to meet this target in "
     "a given month entitles Customer to a service credit equal to 10% of "
     "that month's fees."),
    ("H2", "4. DATA PROCESSING"),
    ("Body",
     "All Customer data is processed in Singapore and Mumbai region data "
     "centers. Vertex acts as a data processor under applicable privacy "
     "regulations. A Data Processing Addendum is incorporated by reference."),
    ("H2", "5. TERMINATION"),
    ("Body",
     "Either party may terminate for material breach with 30 days' written "
     "notice. Customer may export all data within 60 days of termination."),
    ("H2", "6. GOVERNING LAW"),
    ("Body",
     "Singapore law, with disputes resolved by SIAC arbitration in Singapore."),
    ("H2", "7. SIGNATURES"),
    ("Body",
     "<b>VERTEX INDUSTRIES LTD.</b><br/>By: Rajesh Sharma, CEO<br/>"
     "Dated: March 3, 2026"),
    ("Body",
     "<b>PINNACLE HOLDINGS PTE. LTD.</b><br/>By: Mei Lin Tan, Director<br/>"
     "Dated: March 3, 2026"),
]


def build_saas_pdf() -> Path:
    return _render_pdf(
        ROOT / "saas-subscription-agreement.pdf",
        _para_from(_SAAS, _pdf_styles()),
        title="Vertex SaaS Subscription",
    )


# ---------------------------------------------------------------------------
# Doc 8 — employment-offer-letter.pdf  (HR / legal)
# ---------------------------------------------------------------------------


_OFFER = [
    ("Title", "OFFER OF EMPLOYMENT"),
    ("Body",
     "Acme Robotics Inc.<br/>"
     "550 Industrial Park Drive<br/>"
     "San Jose, CA 95134<br/><br/>"
     "March 18, 2026"),
    ("Body",
     "Daniel Park<br/>"
     "1422 Olive Street, Apt 4B<br/>"
     "Mountain View, CA 94041"),
    ("Body", "Dear Daniel,"),
    ("Body",
     "We are pleased to offer you the position of <b>Senior Robotics Engineer</b> "
     "at Acme Robotics Inc., reporting to Dr. Priya Patel, VP of Engineering. "
     "Your anticipated start date is April 15, 2026."),
    ("H2", "1. COMPENSATION"),
    ("Body",
     "Your base annual salary will be USD $185,000, paid in semi-monthly "
     "installments. You will also be eligible for a discretionary annual "
     "bonus targeted at 15% of base salary, subject to company and individual "
     "performance."),
    ("H2", "2. EQUITY"),
    ("Body",
     "Subject to Board approval, you will be granted 20,000 restricted stock "
     "units, vesting over four (4) years with a one-year cliff (25% on the "
     "first anniversary, then 1/48 monthly thereafter)."),
    ("H2", "3. BENEFITS"),
    ("Body",
     "You will be eligible for our standard benefits package including health, "
     "dental, and vision insurance (effective on your start date), 401(k) "
     "with 4% employer match, and 25 days of paid time off per year."),
    ("H2", "4. EMPLOYMENT-AT-WILL"),
    ("Body",
     "Your employment with Acme Robotics is at-will, meaning either you or "
     "the company may terminate the relationship at any time, with or without "
     "cause or notice."),
    ("H2", "5. ACCEPTANCE"),
    ("Body",
     "To accept this offer, please sign below and return by April 1, 2026. "
     "This offer is contingent upon successful completion of a background check."),
    ("Body",
     "Sincerely,<br/><br/>"
     "<b>Jennifer Wallace</b><br/>"
     "Chief People Officer<br/>"
     "Acme Robotics Inc."),
    ("Small", "Accepted by: ______________________ Date: ______________________"),
]


def build_offer_pdf() -> Path:
    return _render_pdf(
        ROOT / "employment-offer-letter.pdf",
        _para_from(_OFFER, _pdf_styles()),
        title="Employment Offer",
        author="Acme Robotics Inc.",
    )


# ---------------------------------------------------------------------------
# Doc 9 — invoice-mar2026.pdf  (financial)
# ---------------------------------------------------------------------------


_INVOICE = [
    ("Title", "INVOICE"),
    ("Small",
     "Vertex Industries Ltd. · Plot 14, MIDC Industrial Area, Pune 411019, "
     "India · GSTIN 27AABCV1234M1Z5"),
    ("Body",
     "<b>Invoice No.</b>: VRX-2026-0317<br/>"
     "<b>Invoice Date</b>: March 31, 2026<br/>"
     "<b>Due Date</b>: May 15, 2026 (net-45 per amendment)<br/>"
     "<b>Currency</b>: USD"),
    ("H2", "BILL TO"),
    ("Body",
     "NorthWind Capital LLC<br/>"
     "120 Broadway, Suite 1200<br/>"
     "New York, NY 10271, USA<br/>"
     "Attn: Sarah Chen, CFO"),
    ("H2", "LINE ITEMS"),
    ("Body",
     "1. Document-intelligence services — Mumbai facility · 89,400 documents "
     "@ USD $0.018/doc = USD $1,609.20<br/>"
     "2. Document-intelligence services — Pune facility · 76,200 documents "
     "@ USD $0.018/doc = USD $1,371.60<br/>"
     "3. Document-intelligence services — Aurangabad facility · 74,400 documents "
     "@ USD $0.018/doc = USD $1,339.20<br/>"
     "4. Enterprise SLA premium (99.95% uptime, MoM) = USD $1,000.00"),
    ("H2", "SUMMARY"),
    ("Body",
     "Subtotal: USD $5,320.00<br/>"
     "Less: First-month discount (10%): USD -$532.00<br/>"
     "<b>Total Due: USD $4,788.00</b>"),
    ("H2", "PAYMENT INSTRUCTIONS"),
    ("Body",
     "Wire to Vertex Industries Ltd., HDFC Bank, Pune Branch, "
     "Account 50100099887766, SWIFT HDFCINBB. Please reference invoice number "
     "VRX-2026-0317 on the wire memo."),
]


def build_invoice_pdf() -> Path:
    return _render_pdf(
        ROOT / "invoice-mar2026.pdf",
        _para_from(_INVOICE, _pdf_styles()),
        title="Vertex Invoice Mar 2026",
        author="Vertex Industries Ltd.",
    )


# ---------------------------------------------------------------------------
# Doc 13 — lab-blood-panel.pdf  (healthcare)
# ---------------------------------------------------------------------------


_LAB = [
    ("Title", "COMPREHENSIVE METABOLIC PANEL — LABORATORY REPORT"),
    ("Small",
     "BayView Diagnostics · CLIA #05D2034567 · Report ID: BVD-2026-118293"),
    ("Body",
     "<b>Patient</b>: Jordan Rivera (MRN 0048213)<br/>"
     "<b>DOB</b>: 1987-09-12 (age 38)<br/>"
     "<b>Sex</b>: Female<br/>"
     "<b>Ordering Provider</b>: Dr. Aaron Bennett, MD (NPI 1234567890)<br/>"
     "<b>Collection Date</b>: April 2, 2026 08:14<br/>"
     "<b>Reported</b>: April 2, 2026 16:22"),
    ("H2", "ANALYTES"),
    ("Body",
     "Glucose, fasting: <b>112 mg/dL</b> (reference 70–99) — <b>HIGH</b><br/>"
     "Sodium: 139 mmol/L (reference 135–145) — normal<br/>"
     "Potassium: 4.1 mmol/L (reference 3.5–5.0) — normal<br/>"
     "Chloride: 102 mmol/L (reference 98–107) — normal<br/>"
     "Bicarbonate (CO₂): 25 mmol/L (reference 22–29) — normal<br/>"
     "BUN: 18 mg/dL (reference 7–20) — normal<br/>"
     "Creatinine: 0.92 mg/dL (reference 0.6–1.1) — normal<br/>"
     "eGFR: 88 mL/min/1.73m² (reference &gt;60) — normal<br/>"
     "Calcium: 9.3 mg/dL (reference 8.6–10.2) — normal<br/>"
     "Total protein: 7.0 g/dL (reference 6.0–8.3) — normal<br/>"
     "Albumin: 4.2 g/dL (reference 3.5–5.0) — normal<br/>"
     "Alkaline phosphatase: 96 U/L (reference 40–129) — normal<br/>"
     "ALT: 28 U/L (reference 7–55) — normal<br/>"
     "AST: 22 U/L (reference 8–48) — normal<br/>"
     "Bilirubin, total: 0.6 mg/dL (reference 0.1–1.2) — normal"),
    ("H2", "PROVIDER COMMENTS"),
    ("Body",
     "Fasting glucose elevated (112 mg/dL). Patient instructed to return for "
     "follow-up HbA1c within two weeks to evaluate for prediabetes. All other "
     "analytes within normal limits."),
    ("Small",
     "Electronically signed by Aaron Bennett, MD on April 2, 2026 at 16:22 PT."),
]


def build_lab_pdf() -> Path:
    return _render_pdf(
        ROOT / "lab-blood-panel.pdf",
        _para_from(_LAB, _pdf_styles()),
        title="Lab Report",
        author="BayView Diagnostics",
    )


# ---------------------------------------------------------------------------
# Doc 15 — resume-software-engineer.pdf  (HR)
# ---------------------------------------------------------------------------


_RESUME = [
    ("Title", "PRIYANKA DESAI"),
    ("Small",
     "priyanka.desai@example.com · +1 (415) 555-0188 · "
     "linkedin.com/in/priyanka-desai · github.com/pdesai · San Francisco, CA"),
    ("H2", "SUMMARY"),
    ("Body",
     "Senior software engineer with 8 years of experience building "
     "distributed systems and ML infrastructure. Most recently led the "
     "feature-store rewrite at Stripe that reduced p99 latency by 62%. "
     "Looking for staff-level IC roles working on developer tools or "
     "ML infrastructure."),
    ("H2", "EXPERIENCE"),
    ("Body",
     "<b>Stripe</b> — Senior Software Engineer, Machine Learning Platform<br/>"
     "January 2022 – Present, San Francisco, CA<br/>"
     "• Led migration of feature store from in-house Postgres-based system "
     "to a new event-sourced platform handling 4.2M features/sec.<br/>"
     "• Designed and shipped the GraphQL API used by 380+ ML engineers, "
     "reducing onboarding time from 3 weeks to 2 days.<br/>"
     "• Mentored 5 engineers, two of whom were promoted to senior."),
    ("Body",
     "<b>Snowflake</b> — Software Engineer II<br/>"
     "August 2019 – December 2021, San Mateo, CA<br/>"
     "• Owned the query optimizer's join-reordering pass; reduced average "
     "query latency on TPC-DS by 18%.<br/>"
     "• Wrote the internal blog post (with 1,400+ views) explaining "
     "Snowflake's micro-partition pruning strategy."),
    ("Body",
     "<b>Microsoft</b> — Software Engineer<br/>"
     "July 2018 – July 2019, Redmond, WA<br/>"
     "• Contributed to the .NET Core garbage collector's region-based "
     "allocator (now shipping in .NET 8)."),
    ("H2", "EDUCATION"),
    ("Body",
     "<b>Indian Institute of Technology Bombay</b> — B.Tech. in Computer "
     "Science and Engineering, 2018. GPA 9.1/10. Department rank 4 of 110."),
    ("H2", "SKILLS"),
    ("Body",
     "Languages: Go, Rust, Python, TypeScript<br/>"
     "Systems: PostgreSQL, Kafka, Redis, gRPC, Kubernetes, Terraform<br/>"
     "ML infra: Kubeflow, MLflow, Ray, Feast, Triton"),
    ("H2", "OPEN SOURCE"),
    ("Body",
     "• Maintainer, <b>feast</b> (3.4k★) — open-source feature store; "
     "shipped the materialization-pipeline rewrite.<br/>"
     "• Contributor, <b>pgvector</b> — wrote the IVFFlat index parallelization "
     "patch (merged in 0.7.0)."),
]


def build_resume_pdf() -> Path:
    return _render_pdf(
        ROOT / "resume-software-engineer.pdf",
        _para_from(_RESUME, _pdf_styles()),
        title="Priyanka Desai — Resume",
        author="Priyanka Desai",
    )


# ---------------------------------------------------------------------------
# Doc 24 — insurance-eob.pdf  (insurance form)
# ---------------------------------------------------------------------------


_EOB = [
    ("Title", "EXPLANATION OF BENEFITS — THIS IS NOT A BILL"),
    ("Small",
     "BlueShield United · Member Services 1-800-555-0142 · "
     "blueshield-united.com"),
    ("Body",
     "<b>Member</b>: Jordan Rivera<br/>"
     "<b>Member ID</b>: BSU-882-441-9907<br/>"
     "<b>Plan</b>: BlueShield Choice PPO 2026<br/>"
     "<b>Claim Number</b>: CLM-2026-04-018724<br/>"
     "<b>Statement Date</b>: April 18, 2026<br/>"
     "<b>Provider</b>: BayView Diagnostics (NPI 1234567890)<br/>"
     "<b>Date of Service</b>: April 2, 2026"),
    ("H2", "SUMMARY OF YOUR CLAIM"),
    ("Body",
     "Amount Billed: $312.00<br/>"
     "Discount applied (in-network): -$148.00<br/>"
     "Allowed Amount: $164.00<br/>"
     "Plan Paid: $131.20<br/>"
     "Your Responsibility (coinsurance 20%): $32.80<br/>"
     "<b>You owe the provider: $32.80</b>"),
    ("H2", "SERVICES"),
    ("Body",
     "Comprehensive Metabolic Panel (CPT 80053)<br/>"
     "Provider charge: $312.00 · Allowed: $164.00 · Plan paid: $131.20 · "
     "Member responsibility: $32.80"),
    ("H2", "DEDUCTIBLE TRACKER"),
    ("Body",
     "Individual deductible: $500.00 · Met YTD: $185.40 · Remaining: $314.60<br/>"
     "Out-of-pocket max: $4,000.00 · Met YTD: $312.20 · Remaining: $3,687.80"),
    ("Small",
     "If you disagree with this determination, you may file an appeal within "
     "180 days. Contact Member Services or visit blueshield-united.com/appeals."),
]


def build_eob_pdf() -> Path:
    return _render_pdf(
        ROOT / "insurance-eob.pdf",
        _para_from(_EOB, _pdf_styles()),
        title="Insurance EOB",
        author="BlueShield United",
    )


# ---------------------------------------------------------------------------
# Docs 10–11 — bank-statement.xlsx + expense-report.xlsx
# ---------------------------------------------------------------------------


def build_bank_statement_xlsx() -> Path:
    out = ROOT / "bank-statement.xlsx"
    book = xlsxwriter.Workbook(str(out))
    _freeze_workbook(book)
    header_fmt = book.add_format({
        "bold": True, "bg_color": "#f4f4f5", "border": 1,
        "align": "left", "valign": "vcenter",
    })
    body_fmt = book.add_format({"border": 1, "valign": "vcenter"})
    money_fmt = book.add_format({"border": 1, "num_format": '"$"#,##0.00'})
    date_fmt = book.add_format({"border": 1, "num_format": "yyyy-mm-dd"})

    sheet = book.add_worksheet("March 2026")
    sheet.set_column(0, 0, 12)   # date
    sheet.set_column(1, 1, 40)   # description
    sheet.set_column(2, 2, 14)   # debit
    sheet.set_column(3, 3, 14)   # credit
    sheet.set_column(4, 4, 14)   # balance

    for col, name in enumerate(["Date", "Description", "Debit", "Credit", "Balance"]):
        sheet.write(0, col, name, header_fmt)

    # NorthWind operating account — March 2026
    import datetime as _dt
    transactions = [
        ("2026-03-01", "Opening balance",                              None,    None,     842_115.20),
        ("2026-03-03", "Wire OUT — Vertex Industries Ltd. (Feb invoice)",  4_320.00,  None,     837_795.20),
        ("2026-03-05", "Wire IN — Pinnacle Holdings (consulting)",     None,    18_500.00, 856_295.20),
        ("2026-03-07", "ACH — AWS hosting (Feb)",                       7_182.55, None,     849_112.65),
        ("2026-03-10", "Payroll — March 1–15",                          188_440.10, None, 660_672.55),
        ("2026-03-12", "Wire IN — Helios Analytics (NDA escrow refund)", None, 5_000.00,  665_672.55),
        ("2026-03-14", "ACH — Snowflake compute",                        12_890.00, None, 652_782.55),
        ("2026-03-18", "Wire OUT — Acme Robotics (proof-of-concept)",    25_000.00, None, 627_782.55),
        ("2026-03-20", "Wire IN — Series B closing tranche",             None,  4_000_000.00, 4_627_782.55),
        ("2026-03-22", "Bank fee — international wire",                  45.00,   None,     4_627_737.55),
        ("2026-03-25", "ACH — Office lease (April)",                     18_900.00, None, 4_608_837.55),
        ("2026-03-28", "Payroll — March 16–31",                          196_220.45, None, 4_412_617.10),
        ("2026-03-31", "Interest earned (operating account)",            None,    412.18,   4_413_029.28),
        ("2026-03-31", "Closing balance",                                None,    None,     4_413_029.28),
    ]
    for r, (d, desc, deb, cred, bal) in enumerate(transactions, start=1):
        sheet.write(r, 0, _dt.date.fromisoformat(d), date_fmt)
        sheet.write(r, 1, desc, body_fmt)
        if deb is not None:
            sheet.write(r, 2, deb, money_fmt)
        else:
            sheet.write(r, 2, "", body_fmt)
        if cred is not None:
            sheet.write(r, 3, cred, money_fmt)
        else:
            sheet.write(r, 3, "", body_fmt)
        sheet.write(r, 4, bal, money_fmt)

    # Account header sheet
    s2 = book.add_worksheet("Account Info")
    s2.set_column(0, 0, 28)
    s2.set_column(1, 1, 36)
    rows = [
        ("Account holder",        "NorthWind Capital LLC"),
        ("Account number (last4)", "**** 7741"),
        ("Routing number",         "021000089"),
        ("Bank",                  "First Republic Bank"),
        ("Statement period",      "March 1, 2026 – March 31, 2026"),
        ("Opening balance (USD)",  842_115.20),
        ("Closing balance (USD)",  4_413_029.28),
        ("Net change (USD)",       3_570_914.08),
    ]
    for r, (k, v) in enumerate(rows):
        s2.write(r, 0, k, header_fmt)
        s2.write(r, 1, v, money_fmt if isinstance(v, (int, float)) else body_fmt)

    book.close()
    return out


def build_expense_report_xlsx() -> Path:
    out = ROOT / "expense-report.xlsx"
    book = xlsxwriter.Workbook(str(out))
    _freeze_workbook(book)
    header_fmt = book.add_format({
        "bold": True, "bg_color": "#f4f4f5", "border": 1,
        "align": "left", "valign": "vcenter",
    })
    body_fmt = book.add_format({"border": 1, "valign": "vcenter"})
    money_fmt = book.add_format({"border": 1, "num_format": '"$"#,##0.00'})
    date_fmt = book.add_format({"border": 1, "num_format": "yyyy-mm-dd"})

    sheet = book.add_worksheet("Expenses")
    sheet.set_column(0, 0, 12)
    sheet.set_column(1, 1, 18)
    sheet.set_column(2, 2, 36)
    sheet.set_column(3, 3, 14)
    sheet.set_column(4, 4, 14)
    sheet.set_column(5, 5, 12)

    for col, name in enumerate(["Date", "Category", "Description", "Amount (USD)",
                                "Reimbursable", "Receipt #"]):
        sheet.write(0, col, name, header_fmt)

    import datetime as _dt
    rows = [
        ("2026-03-04", "Travel",        "United Airlines SFO→BOM (econ)",  1_842.10, "Yes", "R-001"),
        ("2026-03-04", "Travel",        "Lyft to SFO",                        58.30,  "Yes", "R-002"),
        ("2026-03-05", "Lodging",       "Trident Hotel, Mumbai (3 nights)", 624.00,  "Yes", "R-003"),
        ("2026-03-06", "Meals",         "Client dinner — NorthWind team",    312.45,  "Yes", "R-004"),
        ("2026-03-07", "Local transport","Pre-arranged car, Mumbai",          80.00,  "Yes", "R-005"),
        ("2026-03-08", "Travel",        "Indigo IXM→PNQ",                    96.50,  "Yes", "R-006"),
        ("2026-03-08", "Lodging",       "ITC Maratha, Pune (2 nights)",     402.00,  "Yes", "R-007"),
        ("2026-03-10", "Meals",         "Team lunch — Pune office",          78.90,  "Yes", "R-008"),
        ("2026-03-10", "Personal",      "Souvenirs for family",              60.00,  "No",  "R-009"),
        ("2026-03-11", "Travel",        "Indigo PNQ→SFO via DEL",          2_104.75, "Yes", "R-010"),
        ("2026-03-15", "Software",      "Notion Plus subscription (annual)", 96.00,  "Yes", "R-011"),
    ]
    for r, (d, cat, desc, amt, reim, rec) in enumerate(rows, start=1):
        sheet.write(r, 0, _dt.date.fromisoformat(d), date_fmt)
        sheet.write(r, 1, cat, body_fmt)
        sheet.write(r, 2, desc, body_fmt)
        sheet.write(r, 3, amt, money_fmt)
        sheet.write(r, 4, reim, body_fmt)
        sheet.write(r, 5, rec, body_fmt)

    # Summary sheet
    s2 = book.add_worksheet("Summary")
    s2.set_column(0, 0, 28)
    s2.set_column(1, 1, 22)
    summary = [
        ("Employee",                "Sarah Chen"),
        ("Employee ID",             "NW-0042"),
        ("Department",              "Finance"),
        ("Trip purpose",            "Vertex Q1 partnership review (Mumbai + Pune)"),
        ("Trip dates",              "March 4 – March 11, 2026"),
        ("Total submitted (USD)",   5_754.00),
        ("Reimbursable (USD)",      5_694.00),
        ("Personal (USD)",          60.00),
        ("Submitted",               "March 14, 2026"),
        ("Approver",                "Michael Park (CFO)"),
    ]
    for r, (k, v) in enumerate(summary):
        s2.write(r, 0, k, header_fmt)
        s2.write(r, 1, v, money_fmt if isinstance(v, (int, float)) else body_fmt)

    book.close()
    return out


# ---------------------------------------------------------------------------
# Plain-text + Markdown + EML docs
# ---------------------------------------------------------------------------


_DISCHARGE_SUMMARY = """\
WESTSIDE GENERAL HOSPITAL
DISCHARGE SUMMARY

Patient: Jordan Rivera
MRN: 0048213
DOB: 1987-09-12
Sex: Female

Admission Date: 2026-04-15
Discharge Date: 2026-04-17
Length of stay: 2 days
Attending Physician: Aaron Bennett, MD
Discharge disposition: Home, with PCP follow-up

REASON FOR ADMISSION
--------------------
Acute right-flank pain, hematuria. Imaging on admission showed a 4 mm
non-obstructing right ureteric calculus. Pain was managed conservatively;
stone passed spontaneously prior to discharge.

HOSPITAL COURSE
---------------
The patient presented to the emergency department on 2026-04-15 at 03:42
with sudden-onset right-sided flank pain and gross hematuria. Initial vitals
were stable (BP 128/82, HR 96, T 37.2°C, SpO2 99% on room air).

Non-contrast CT abdomen/pelvis identified a 4 mm non-obstructing calculus
in the proximal right ureter, no hydronephrosis. Urinalysis demonstrated
30-40 RBC/HPF, no nitrites, no leukocyte esterase. Serum creatinine 0.92
mg/dL (baseline). Urine culture pending at discharge (no antibiotics
initiated).

Pain was controlled with IV ketorolac 30 mg q6h and PO acetaminophen 1 g
q6h PRN. The patient reported passage of a small calculus on 2026-04-16
at 14:18 with immediate symptomatic relief. Repeat ultrasound on 2026-04-17
confirmed no residual stone.

DISCHARGE MEDICATIONS
---------------------
1. Tamsulosin 0.4 mg PO daily for 14 days (facilitate passage of any
   subclinical residual fragments)
2. Acetaminophen 500 mg PO q6h PRN pain
3. Ibuprofen 400 mg PO q8h PRN pain (with food)

DISCHARGE INSTRUCTIONS
----------------------
- Increase fluid intake to 2.5 L/day for the next 30 days.
- Strain urine for any further stones; bring to PCP for compositional
  analysis if recovered.
- Return to ED for fever > 38.5°C, severe pain, intractable nausea/vomiting,
  or anuria.
- Follow up with Dr. Bennett (PCP) within 2 weeks for repeat metabolic
  panel and HbA1c (noting prior elevated fasting glucose on 2026-04-02).
- Urology referral placed (Dr. Lisa Wong) for outpatient evaluation if any
  recurrence within 12 months.

DIAGNOSES
---------
1. Right ureterolithiasis, passed (N20.1)
2. Gross hematuria, resolved (R31.0)
3. Prediabetes mellitus, pending HbA1c (R73.03)

Electronically signed by Aaron Bennett, MD on 2026-04-17 at 11:45.
"""


def build_discharge_summary_txt() -> Path:
    out = ROOT / "discharge-summary.txt"
    out.write_text(_DISCHARGE_SUMMARY)
    return out


_BUG_REPORT = """\
[BUG-1234] Worker crashes with NullPointerException when processing
attachments larger than 10 MB

Reporter:        priya.menon@example.com
Date reported:   2026-03-22 14:18 UTC
Component:       attachment-ingestor (v2.4.1)
Severity:        S2 (major; affects 1.8% of production traffic)
Priority:        P1
Status:          Open
Assigned to:     ramesh.gupta@example.com
Affects:         production (us-east-1, eu-west-1, ap-south-1)
Found in build:  attachment-ingestor-2.4.1-rc.3 deployed 2026-03-20

ENVIRONMENT
-----------
- attachment-ingestor v2.4.1 (commit a8f2c19)
- Kafka 3.7.2
- Postgres 16.2 (Aurora)
- JVM: OpenJDK 21.0.4
- Heap: -Xmx 8192m (per pod, 12 pods per region)

STEPS TO REPRODUCE
------------------
1. Submit a job with an attachment > 10 MB. Reliable repro at 12 MB.
2. Observe the worker pod logs in Datadog.
3. Within 60 seconds the worker emits NullPointerException at
   AttachmentProcessor.extractMetadata (line 287), then restarts.

EXPECTED
--------
Attachment is processed without error; metadata is extracted and the job
moves to state=processed.

ACTUAL
------
Worker crashes with the following stack trace (truncated):

    java.lang.NullPointerException: Cannot invoke
    "io.minio.GetObjectResponse.headers()" because "response" is null
        at AttachmentProcessor.extractMetadata(AttachmentProcessor.java:287)
        at AttachmentProcessor.handle(AttachmentProcessor.java:142)
        at WorkerLoop.processBatch(WorkerLoop.java:88)
        ...

ROOT CAUSE (HYPOTHESIS)
-----------------------
PR #4421 ("upgrade minio-java to 8.5.10") changed the behavior of
getObject() so that when the object exceeds the configured maxObjectSize
(default 10 MB in 8.5.10, was 100 MB in 8.4.x) the call now returns null
instead of throwing. extractMetadata does not null-check before calling
.headers().

WORKAROUND
----------
Setting maxObjectSize=104857600 in MinioClientConfig appears to suppress
the crash; needs verification against the SDK contract.

NEXT STEPS
----------
- Confirm the SDK behavior change with minio-java maintainers.
- Add null-check + explicit oversized-object error in AttachmentProcessor.
- Backfill 1,847 jobs in dead-letter queue once fix lands.
- Postmortem template attached (separate doc).
"""


def build_bug_report_txt() -> Path:
    out = ROOT / "bug-report-1234.txt"
    out.write_text(_BUG_REPORT)
    return out


_QUARTERLY_SUMMARY = """\
# NorthWind Capital — Q1 2026 Financial Summary

> Prepared by Sarah Chen, CFO · April 15, 2026 · For board review

## Headline numbers

- **Revenue**: $14.2M (up 38% YoY, ahead of plan by $1.1M)
- **Gross margin**: 71% (vs 68% in Q4 2025)
- **Operating expenses**: $11.6M
- **Net income**: $1.4M
- **Cash on hand**: $4.4M operating + $4M Series B closing tranche = **$8.4M total**
- **Burn (net)**: $0.4M/month average across Q1 2026
- **Runway at current burn**: 21 months

## Revenue breakdown

| Line | Q1 2026 (USD) | YoY | Notes |
|---|---:|---:|---|
| Subscription — Enterprise | $9.8M | +42% | Vertex Industries renewed at $4.32k/mo |
| Subscription — Growth     | $2.6M | +28% | Pinnacle Holdings closed in March |
| Professional services     | $1.4M | +51% | Mostly Helios Analytics POC |
| Other                     | $0.4M | -12% | Legacy reporting fees, winding down |
| **Total**                 | **$14.2M** | **+38%** |  |

## Major Q1 milestones

1. **Series B closed March 20, 2026**: $4M tranche received from First
   Capital Ventures. Total Series B = $12M; remaining $8M expected
   Q2/Q3 2026 tied to ARR milestones.
2. **Vertex MSA amended (April 1 effective)**: indemnification cap
   raised to $50M, payment terms extended to net-45, Aurangabad facility
   added. Annual commit projected at $51,840.
3. **First international customer**: Pinnacle Holdings Pte. Ltd. signed
   a 12-month Business-tier subscription for $3,300/month.

## Risks & watch items

- **Glucose** — sorry, **gross margin** dipped briefly to 65% in early
  February due to AWS spot-pricing changes; recovered by month-end.
- **Vertex SLA breach in February** (uptime 99.82% vs 99.95% committed)
  triggered a $1,000 service credit. First credit issued since the
  contract began.
- **Customer concentration**: top 3 customers = 64% of MRR. Sales
  pipeline being prioritized to bring this under 50% by Q3.

## Q2 forecast

| Metric | Q2 2026 forecast | vs Q1 |
|---|---:|---:|
| Revenue | $16.8M | +18% |
| Gross margin | 72% | +1pp |
| Op-ex | $12.4M | +7% |
| Net income | $2.0M | +43% |
"""


def build_quarterly_summary_md() -> Path:
    out = ROOT / "quarterly-financial-summary.md"
    out.write_text(_QUARTERLY_SUMMARY)
    return out


_JOB_DESCRIPTION = """\
# Staff Data Scientist — Risk & Fraud

Helios Analytics Inc. · San Francisco, CA (Hybrid, 3 days in office) ·
Full-time

## About the role

We are hiring a staff-level data scientist to lead the modeling team
inside our Risk & Fraud organization. You will own model architecture,
mentor a team of 4 ML engineers, and partner closely with Risk Operations,
Engineering, and Compliance.

## Compensation

- **Base salary**: $245,000 – $290,000
- **Equity**: 0.04% – 0.08% (vesting 4 years, 1-year cliff)
- **Bonus target**: 20% of base
- **Benefits**: medical / dental / vision (100% employer-paid premiums),
  401(k) with 6% match, $4,000 annual learning budget, 30 PTO days

## What you'll do

- Design and ship the next generation of our transaction-risk model
  (currently a gradient-boosted ensemble; we are evaluating tabular
  transformers).
- Build the feature-engineering platform that the broader risk team
  uses for experimentation and online serving.
- Lead 1–2 major model-architecture reviews per quarter as the
  team's most senior IC.
- Partner with Compliance to shape model-fairness reporting that
  satisfies CFPB Reg B / ECOA constraints.
- Mentor and grow 4 senior data scientists; serve as the recruiting
  lead for the team's next 3 hires.

## Required

- 8+ years of ML / data-science experience, including 3+ years in
  fraud, risk, or adversarial domains.
- Production ownership of at least one model serving > 10M decisions
  per day.
- Demonstrated ability to drive technical strategy across multiple
  teams.
- Deep familiarity with Python, PyTorch or JAX, and a modern SQL
  warehouse (BigQuery / Snowflake / Redshift).
- Comfortable communicating technical trade-offs to executive and
  regulator audiences.

## Nice to have

- Experience with online learning / contextual bandits.
- Prior work on payments fraud or AML transaction monitoring.
- Open-source contributions to mainstream ML libraries.

## Hiring panel

- **Hiring manager**: Dr. Aisha Khan, VP of Risk Analytics
- **Recruiter**: Marcus Webb (marcus.webb@helios-analytics.com)
- **Target start date**: June 1, 2026

## Process

1. Recruiter screen (30 min)
2. Hiring manager interview (60 min)
3. Take-home modeling exercise (4 hours, paid)
4. Onsite — 5 interviews over one day:
   - System design (60 min)
   - ML modeling & evaluation (60 min)
   - Coding (60 min)
   - Cross-functional collaboration (45 min)
   - Executive panel (45 min)
5. Reference checks + offer.
"""


def build_job_description_md() -> Path:
    out = ROOT / "job-description-data-scientist.md"
    out.write_text(_JOB_DESCRIPTION)
    return out


_PERF_REVIEW = """\
# 2025 Annual Performance Review — Sarah Chen, CFO

**Reviewer**: Michael Park, CEO
**Review period**: January 1, 2025 – December 31, 2025
**Date**: January 28, 2026

## Overall rating: **Exceeds Expectations** (4 of 5)

## Quantitative ratings

| Competency | Rating | Notes |
|---|---|---|
| Strategic financial leadership | 5 / 5 | Led Series B prep flawlessly |
| Operational rigor              | 4 / 5 | Month-end close compressed from 12 to 6 business days |
| Stakeholder management         | 4 / 5 | Excellent investor communication |
| Team building                  | 4 / 5 | Hired 3 senior FP&A leads; one regretted attrition |
| Technical accuracy             | 5 / 5 | Zero material errors in 2025 financials |

## Key accomplishments (2025)

1. **Series B preparation**: Built the data room, financial model, and
   investor narrative that closed a $12M Series B at a $94M post-money
   valuation. The dilution outcome was 14% versus the 18% modeled in
   the planning case.
2. **Audit clean opinion**: Successfully managed the company's first
   external audit (by EY); zero material weaknesses identified.
3. **Treasury optimization**: Migrated operating cash from a 2.1% APY
   account to a laddered T-bill structure averaging 4.7% APY, adding
   approximately $140K of interest income for the year.
4. **Internal controls**: Implemented SOX-light controls in anticipation
   of public-company readiness; built segregation-of-duties matrix
   covering 47 financial processes.

## Areas to develop in 2026

1. **Public speaking polish**: Two of the Series B presentations went
   long; investor feedback mentioned "could be more concise." Suggest
   working with the executive coach we recently engaged.
2. **People manager bandwidth**: With three new directs reporting to
   you, prioritize delegating tactical close work so you can focus on
   strategic finance partnerships with Sales and Eng.

## 2026 OKRs (proposed)

- **O1**: Land $20M ARR by year-end (currently $8M ARR exit Q1 2026).
- **O2**: Close Series C bridge of $20M+ at $200M+ post-money by Q3.
- **O3**: Hire and onboard a VP of Finance reporting to you, freeing
  your bandwidth for strategic work.

## Compensation actions

- **Base salary**: increased from $240,000 to $265,000 (+10.4%)
- **Refresh grant**: 8,000 RSU (4-year vest, monthly after cliff)
- **Bonus 2025 (paid Feb 2026)**: $48,000 (target $36,000 × 1.33 multiplier)

---

_Acknowledged by employee on January 30, 2026._
"""


def build_perf_review_md() -> Path:
    out = ROOT / "performance-review-2025.md"
    out.write_text(_PERF_REVIEW)
    return out


_POSTMORTEM = """\
# Incident Postmortem — Vertex AI Platform outage, March 18, 2026

**Authors**: Ramesh Gupta (SRE on-call), Priya Menon (Platform engineering lead)
**Status**: Final
**Reviewed by**: Rajesh Sharma (CEO), Maya Iyer (CTO)
**Date**: March 24, 2026

## Summary

On March 18, 2026 between **04:12 and 06:42 UTC**, the Vertex AI Platform
returned 5xx errors for all document-ingestion requests in the
**ap-south-1** (Mumbai) region. The outage lasted **2 hours 30 minutes**
and affected approximately **3,400** customer-submitted documents,
including all NorthWind Capital traffic for that window. Service was
restored after a manual database failover.

Customer impact: **two enterprise customers** filed support tickets
referencing the outage. SLA credits owed: **NorthWind $1,000** (uptime
breach for the month), **Pinnacle Holdings $0** (within SLA threshold).

## Timeline (UTC)

| Time | Event |
|---|---|
| 04:12 | Primary RDS Postgres in ap-south-1 begins reporting elevated CPU |
| 04:18 | Connection pool exhaustion on attachment-ingestor pods |
| 04:24 | First 503s returned to customers; PagerDuty fires |
| 04:30 | On-call (Ramesh) acks alert; opens incident channel |
| 04:42 | Root cause hypothesis: runaway query from new analytics job |
| 04:58 | Analytics job killed; CPU drops to 60% |
| 05:14 | New connections still failing — pool not recovering |
| 05:32 | Failover to standby RDS initiated |
| 05:48 | Failover complete; ingestion restored at 5% throughput |
| 06:12 | Throughput at 80% as backed-up jobs drain |
| 06:42 | Throughput at 100%; incident declared resolved |
| 09:00 | Customer communication sent to NorthWind, Pinnacle Holdings |

## Root cause

A new analytics job introduced in commit `b14e9c2` (deployed
March 17, 2026 at 22:40 UTC) ran an unbounded `SELECT` against the
`document_chunks` table without a `LIMIT` clause. By 04:12 UTC the next
day, the query had accumulated enough rows to saturate Postgres CPU and
hold all connections from the application pool.

Compounding factors:

1. The pgbouncer connection pool's `server_idle_timeout` was set to
   1 hour, so connections held by the analytics process were not
   reaped quickly enough to relieve pressure.
2. The RDS standby was 38 seconds behind primary (well within tolerance
   but contributing to data lag after failover).
3. The on-call runbook for "connection pool exhaustion" was 9 months
   stale and pointed to a since-renamed dashboard.

## Action items

| # | Action | Owner | Due | Status |
|---|---|---|---|---|
| 1 | Add CI guard rejecting any SQL in `analytics/` without explicit `LIMIT` | Priya Menon | March 31 | Open |
| 2 | Lower `server_idle_timeout` to 5 minutes in pgbouncer config | Ramesh Gupta | March 25 | **Done** |
| 3 | Refresh "connection pool exhaustion" runbook with current dashboards | Ramesh Gupta | April 1 | Open |
| 4 | Migrate analytics jobs to read replica instead of primary | Maya Iyer | April 15 | Open |
| 5 | Send SLA credit notice to NorthWind for $1,000 | Sarah Chen (CFO Vertex) | March 26 | **Done** |
| 6 | Add synthetic monitoring for ingestion endpoint (every 60s, 3 regions) | Priya Menon | April 8 | Open |

## What went well

- On-call ack within 6 minutes of page.
- Failover playbook was up-to-date and executed cleanly.
- Customer communication, while delayed, was honest and specific.

## What didn't go well

- Runbook staleness slowed the diagnosis by an estimated 20 minutes.
- No synthetic monitoring meant we relied on customer-affecting errors
  to detect the outage.
- The analytics job was deployed Friday evening with no Monday-morning
  follow-up review.

## Glossary

- **SRE**: Site Reliability Engineering
- **RDS**: AWS Relational Database Service
- **SLA**: Service Level Agreement (Vertex commits to 99.95% monthly uptime)
"""


def build_postmortem_md() -> Path:
    out = ROOT / "incident-postmortem.md"
    out.write_text(_POSTMORTEM)
    return out


_STANDUP = """\
# Vertex Platform Team — Weekly Standup, 2026-04-20

**Attendees**: Priya Menon (lead), Ramesh Gupta, Vikram Iyer, Anika Rao,
Devansh Kumar, Sneha Patel

**Notes by**: Anika Rao
**Next meeting**: 2026-04-27 09:30 IST

## Last week — completed

- **Priya**: Shipped the `analytics/` lint rule from postmortem action
  item #1. Caught 14 existing offenders during retro-scan; assigned to
  individual owners.
- **Ramesh**: Pgbouncer config rollout in all three regions (action
  item #2). Verified via load test; no regressions.
- **Vikram**: Closed the NorthWind feature request for per-document
  audit log export. Shipped behind feature flag, NorthWind validated.
- **Anika**: Migrated the integration test suite from CircleCI to
  Buildkite; build time down from 38 min to 14 min on hot cache.
- **Devansh**: Onboarded Pinnacle Holdings; their first ingestion
  batch (412 docs) completed on time.
- **Sneha**: Wrote the design doc for the chunk-text dedup; Priya to
  review by Friday.

## This week — committed

- **Priya**: Review Sneha's chunk-text dedup design doc; schedule the
  technical review with Maya Iyer for Thursday.
- **Ramesh**: Implement the synthetic monitor (action item #6 from
  postmortem). Target: deployed in staging by Thursday.
- **Vikram**: Start scoping the multi-region failover automation.
- **Anika**: Pair with Devansh on the Helios Analytics evaluation
  pipeline; they want a POC by April 30.
- **Devansh**: Continue Helios POC; also support Sneha if she needs
  Postgres expertise on the dedup project.
- **Sneha**: Revise the dedup design doc based on Priya's review;
  start the prototype branch.

## Blockers

- **Vikram**: Multi-region failover automation needs sign-off from
  Maya on the proposed CRDT-based state machine. Will request 30 min
  on Wed.
- **Anika**: Buildkite spot-instance availability has been flaky; have
  filed a support ticket with their team. Workaround: fallback to
  on-demand for critical-path jobs.

## Announcements

- **All-hands Friday at 10:00 IST**: Rajesh will share the Q1 numbers
  and the Series B closing milestones.
- **New hire**: Ankit Sharma joins the Platform team on May 5 — he'll
  pair with Devansh for his first month.
- **Open positions**: Still hiring for one Senior SRE in Pune and one
  Staff Engineer (any location). Referrals appreciated.
"""


def build_standup_md() -> Path:
    out = ROOT / "weekly-standup-notes.md"
    out.write_text(_STANDUP)
    return out


_RFC = """\
# RFC: Source-Offset Citation Resolver (KB-RFC-008)

**Authors**: Vikram Iyer (vikram.iyer@vertex.com)
**Status**: Draft → seeking review
**Filed**: 2026-03-29
**Reviewers**: Priya Menon, Maya Iyer
**Target merge**: 2026-04-15

## Problem

When users click an extracted fact (mention, field, triple) in the
doc-detail UI, we want to highlight the exact source location in the
original file. Today, the right-pane click publishes a fuzzy text-search
string; the source pane does first-substring-match. This produces wrong
highlights when the snippet collides (e.g. "1.5" finds the first "1.5"
on the page, not the cited "1.5%" interest rate).

## Goals

1. Pixel-precise highlight for every L2/L3/L4 extraction where the LLM
   returned verbatim text.
2. Honest "no source location" UI when the LLM paraphrased.
3. No new LLM prompt design ("offset-aware LLM") — keep working with
   what the existing extractors return.

## Non-goals

- PDF bbox precision (Wave C; needs Docling layout persistence).
- Backfill for pre-RFC data (separate one-shot script).
- Cross-doc citation aggregation (Wave B item, not blocked by this).

## Proposal

After each LLM extraction stage, run a **deterministic resolver** that
locates the LLM's verbatim snippet inside the chunk that was sent to
the LLM. Persist `(source_chunk_id, source_char_start, source_char_end)`
on the extraction row.

### Schema

```sql
ALTER TABLE extracted_mentions
    ADD COLUMN source_chunk_id UUID REFERENCES chunks(id),
    ADD COLUMN source_char_start INT,
    ADD COLUMN source_char_end INT;
-- and analogous columns on proposed_fields, atomic_units, extracted_triples
```

### Resolver

Two-pass match against the chunk text:

1. **Exact substring** — `chunk_text.find(snippet)`. Most LLM output
   is verbatim; this catches the easy case.
2. **Whitespace-normalized** — collapse `\\s+` to single space on both
   sides; remember offset map to translate the normalized match back
   to the original.

Returns `None` when neither pass matches. UI surfaces "no source
location" rather than mis-highlighting.

### UI behavior

| Found | Behavior |
|---|---|
| Yes | Fetch `/chunks/:id`; slice `[char_start:char_end]`; highlight the verbatim string in the format-specific viewer (text `<mark>`, xlsx `<td>` outline, PDF.js text-layer span). |
| No  | Show "no source location stored — best-effort search" + fall back to fuzzy text-search. |

## Considered alternatives

### Alt 1: Offset-aware LLM

Prompt the LLM to return `{text, start, end}` for each extracted item.

**Pros**: Single round-trip, no resolver code.

**Cons**: LLMs are poor at character offsets, off-by-one errors are
common, prompt complexity grows, validation overhead. Output schema
gets noisier. Rejected.

### Alt 2: Store the raw chunk text on each extraction row

Denormalize chunk text into each mention/field row.

**Pros**: UI doesn't need a `/chunks/:id` round-trip.

**Cons**: Massive data duplication (chunks are ~2KB, mentions are
~50B), schema bloat. Rejected.

### Alt 3: Compute offsets at retrieval time only

Run the resolver at API-read time instead of extraction-write time.

**Pros**: No schema migration.

**Cons**: Repeated work per request, slower API, doesn't scale with
read volume. Rejected.

## Migration plan

1. Land schema migration as additive nullable columns.
2. Ship worker resolver wired into all 4 extractors.
3. Run one-shot backfill script (`scripts/backfill_source_positions.py`)
   on existing rows.
4. Update UI to prefer exact citations, fall back to text-search.

## Rollout

- **Week 1**: schema migration + resolver lands. No UI change.
- **Week 2**: UI uses the new positions for newly-extracted data.
- **Week 3**: Backfill old data + flip UI to use positions for everything.

## Open questions

- How do we handle the 3% of fields where the LLM paraphrases? Today
  the UI shows "no source location"; should we attempt fuzzy fallback
  with a confidence indicator?
- What's the migration story for the `extracted_entities.citations`
  jsonb (which already has `{field: chunk_id}` per Design 5)? Do we
  also extend that to include char offsets?
- Once Wave C ships per-element bbox in `raw_pages.layout_elements`,
  do we add a `source_bbox` column or keep bbox lookup as a derived
  read-time operation?
"""


def build_rfc_md() -> Path:
    out = ROOT / "api-design-rfc.md"
    out.write_text(_RFC)
    return out


_PRESS_RELEASE = """\
# Vertex Industries Announces $12M Series B and Expanded Partnership with NorthWind Capital

**For immediate release**
**Pune, India — April 22, 2026**

Vertex Industries Ltd., a leading provider of document-intelligence
infrastructure, today announced the successful closing of its **$12
million Series B funding round** led by First Capital Ventures, with
participation from existing investors Bharat Innovation Partners and
Anchor Bay Holdings.

The financing values Vertex at $94 million post-money. Funds will be
used to expand the company's engineering team in Pune and Mumbai, open
a new R&D facility in Aurangabad, and accelerate development of its
multi-modal document understanding platform.

Vertex also announced an **expansion of its partnership with NorthWind
Capital LLC**, the New York-based investment firm and Vertex's largest
customer. Under an amendment signed April 1, 2026, NorthWind has
committed to **240,000 documents per month** across all three Vertex
service regions (Mumbai, Pune, Aurangabad) — a 3.2× increase over the
prior commitment.

> "Vertex has been a critical partner as we modernize NorthWind's
> due-diligence workflow. The expansion reflects the value their
> platform delivers and our confidence in their team."
>
> — Sarah Chen, CFO, NorthWind Capital LLC

> "This funding lets us go deeper on the technical roadmap our
> customers have asked for, particularly around multi-language support
> and on-premises deployment options for regulated industries."
>
> — Rajesh Sharma, CEO, Vertex Industries Ltd.

> "Vertex is operating in one of the most exciting sectors of enterprise
> AI. Their growth metrics, customer love, and engineering culture made
> this an easy investment decision."
>
> — Anjali Krishnan, General Partner, First Capital Ventures

## About Vertex Industries

Founded in 2022, Vertex Industries builds document-intelligence
infrastructure used by financial services, legal, and healthcare
organizations to extract, classify, and query unstructured documents at
scale. The company serves customers in 14 countries and processes more
than 12 million documents per month. Vertex is headquartered in Pune,
India, with engineering offices in Mumbai and (as of Q3 2026)
Aurangabad. More at https://vertex.example.com.

## About NorthWind Capital

NorthWind Capital LLC is a $3.2B AUM private investment firm focused on
mid-market industrial businesses. Founded in 2018 and headquartered in
New York, NorthWind manages capital on behalf of family offices,
endowments, and high-net-worth individuals.

## Media contacts

**Vertex Industries**:
Anika Rao, Head of Communications
press@vertex.example.com
+91 (0) 20 5555 0188

**First Capital Ventures**:
Carlos Mendez
carlos@firstcapital.example.com

###
"""


def build_press_release_md() -> Path:
    out = ROOT / "press-release-2026-q1.md"
    out.write_text(_PRESS_RELEASE)
    return out


_CASE_STUDY = """\
# Case Study: How NorthWind Capital Cut Diligence Time by 73% with Vertex

> _"Before Vertex, our diligence team spent four weeks per target
> reading through 6,000+ pages of contracts, lease agreements, and
> regulatory filings. With Vertex, we surface every material clause in
> under a day."_
>
> — Sarah Chen, CFO, NorthWind Capital LLC

## The customer

**NorthWind Capital LLC** is a New York-based private investment firm
managing $3.2B AUM. Their core workflow involves evaluating mid-market
industrial acquisitions — a process that traditionally requires
hundreds of analyst-hours per target spent reading and tagging legal,
financial, and operational documents.

## The challenge

Each diligence engagement at NorthWind requires the analyst team to
process **between 1,000 and 8,000 documents** — typically a mix of
PDFs (contracts, audited financials), Excel models (forecasts), and
emails (correspondence with counterparties).

Pre-Vertex, NorthWind used a combination of e-discovery software and
manual review. The pain points:

- **Time**: 4–6 weeks per target, with three analysts assigned.
- **Recall**: Hand review missed ~12% of material clauses in spot
  audits.
- **Consistency**: Different analysts categorized clauses differently;
  rework was common during senior partner review.
- **Cost**: ~$45K of fully-loaded analyst time per engagement.

## The Vertex implementation

NorthWind onboarded with Vertex in **November 2025**. The rollout took
**6 weeks** from contract signing to first production use:

1. Initial integration with NorthWind's existing document repository
   (SharePoint + S3).
2. Configuration of NorthWind's bespoke clause taxonomy (171 clause
   types tracked).
3. Two pilot diligence engagements run in parallel with manual review
   for accuracy benchmarking.

## The results

After six months of production use across **24 diligence engagements**:

| Metric | Pre-Vertex | With Vertex | Change |
|---|---|---|---|
| Time per engagement | 4–6 weeks | 8–12 days | **−73%** |
| Material-clause recall | 88% | 99.4% | +11.4pp |
| Analyst-hours per target | ~480 | ~120 | **−75%** |
| Cost per engagement (analyst) | $45K | $11K | **−$34K** |
| Engagements completed in 2025 | 18 | 24 (target: 30) | +33% |

NorthWind's CFO estimates the partnership has freed approximately
**$600,000 of annualized analyst capacity**, which has been redeployed
to portfolio-company support and new-fund formation work.

## The expansion

In April 2026, NorthWind signed an amendment to their original MSA,
increasing their committed volume from **75,000 documents per month**
to **240,000 documents per month** and extending Vertex's service
geography to include the **Aurangabad facility** in addition to Mumbai
and Pune.

## What's next

NorthWind and Vertex are now collaborating on:

- A custom **clause-anomaly model** trained on NorthWind's historical
  diligence outcomes (Q2 2026 target).
- **Counterparty risk scoring** that synthesizes data across multiple
  documents in an engagement (Q3 2026 target).
- **Multi-language support** for cross-border deals (currently in
  beta).

---

_Vertex Industries Ltd. is a document-intelligence platform serving
financial services, legal, and healthcare customers worldwide.
Learn more at vertex.example.com._
"""


def build_case_study_md() -> Path:
    out = ROOT / "customer-case-study.md"
    out.write_text(_CASE_STUDY)
    return out


_IT_INCIDENT_EML = """\
Message-ID: <20260318062300.94821@vertex.example.com>
Date: Wed, 18 Mar 2026 06:42:00 +0000
From: Ramesh Gupta <ramesh.gupta@vertex.example.com>
To: status-updates@vertex.example.com
Cc: NorthWind Ops <ops@northwind-capital.com>, Pinnacle Ops <ops@pinnacle-holdings.com>
Subject: [RESOLVED] Mumbai region ingestion outage — March 18, 2026 04:12-06:42 UTC
Content-Type: text/plain; charset=utf-8

All —

The Vertex AI Platform ingestion outage in the ap-south-1 (Mumbai)
region is now RESOLVED.

Duration: 04:12 UTC to 06:42 UTC (2h 30m)
Customer impact: ~3,400 documents queued or failed during the window.
Affected enterprise customers: NorthWind Capital, Pinnacle Holdings.

WHAT HAPPENED
A new analytics job deployed yesterday at 22:40 UTC ran an unbounded
SELECT against the document_chunks table, saturating the primary RDS
Postgres CPU. Connection pool exhaustion cascaded through the
attachment-ingestor pods, causing 503 responses for all ingestion
requests.

WHAT WE DID
1. Killed the runaway analytics job (05:02 UTC).
2. Failed over to the standby RDS Postgres (05:48 UTC complete).
3. Drained the backed-up job queue (06:42 UTC, 100% throughput restored).

ALL queued documents from the outage window have been re-processed
successfully as of 07:10 UTC. No customer documents were lost.

SLA IMPACT
NorthWind: monthly uptime fell to 99.82% (vs 99.95% committed). Per
your MSA Section 5, you are entitled to a service credit of $1,000,
which will appear on your March invoice.

Pinnacle Holdings: monthly uptime remains within your 99.9% SLA. No
credit owed.

POSTMORTEM
A full postmortem will be published within 5 business days per our
incident-response policy. Six action items have been identified; two
are already complete.

If you have any questions, please reach out to your account team or
reply to this thread directly.

Apologies for the disruption.

—
Ramesh Gupta
Senior SRE, Vertex Industries Ltd.
ramesh.gupta@vertex.example.com
On-call rotation: ap-south primary

----- Forwarded message -----
From: PagerDuty <noreply@pagerduty.com>
Date: Wed, 18 Mar 2026 04:30:00 +0000
Subject: [TRIGGERED] vertex-ingestion-mumbai · 503 error rate elevated

Severity: high
Service: vertex-ingestion-mumbai
Region: ap-south-1
Alert source: Datadog monitor "ingestion-503-rate-mumbai"

Acknowledged by: Ramesh Gupta at 04:30 UTC.

----- Forwarded message -----
From: NorthWind Ops <ops@northwind-capital.com>
Date: Wed, 18 Mar 2026 05:42:00 +0000
Subject: [URGENT] Our pipeline shows zero ingestion since 04:15 UTC — is there an issue?

Hi Vertex team,

We're seeing zero successful document ingestions from our New York
pipeline since approximately 04:15 UTC this morning. All requests are
returning 503 errors. Is there an ongoing incident? This affects our
diligence engagement that has a deliverable due Thursday.

Please advise.

— Operations team, NorthWind Capital
"""


def build_it_incident_eml() -> Path:
    out = ROOT / "it-incident-thread.eml"
    out.write_text(_IT_INCIDENT_EML)
    return out


# ---------------------------------------------------------------------------
# build_all() — runs every generator. Used by `python -m demo-corpus.build`.
# ---------------------------------------------------------------------------


def build_all() -> list[Path]:
    return [
        # existing (PR1)
        build_pdf(),
        build_xlsx(),
        # plain-text/md/eml docs were checked in directly; build_all()
        # leaves them in place. (vertex-amendment.txt, vertex-eval-notes.md,
        # vertex-sales-thread.eml)

        # PR3 — broader corpus
        build_nda_pdf(),
        build_saas_pdf(),
        build_offer_pdf(),
        build_invoice_pdf(),
        build_lab_pdf(),
        build_resume_pdf(),
        build_eob_pdf(),

        build_bank_statement_xlsx(),
        build_expense_report_xlsx(),

        build_discharge_summary_txt(),
        build_bug_report_txt(),

        build_quarterly_summary_md(),
        build_job_description_md(),
        build_perf_review_md(),
        build_postmortem_md(),
        build_standup_md(),
        build_rfc_md(),
        build_press_release_md(),
        build_case_study_md(),

        build_it_incident_eml(),
    ]


if __name__ == "__main__":
    for p in build_all():
        print(f"wrote {p}")
