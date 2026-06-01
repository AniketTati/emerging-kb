#!/usr/bin/env python3
"""Generate file-type-diverse finance fixtures for the demo seed corpus.

Produces 9 docs into demo-corpus/domains/finance/docs/ that interconnect with
the existing markdown/email corpus (Acme Corp / HDFC / Vertex / Apollo / Aniket
Desai) so the demo showcases cross-format identity resolution + the full
file-type mix the spec asks for:

  * 3 spreadsheets (.xlsx, openpyxl)         — digital, structured
  * 3 digital PDFs (.pdf, fpdf2, text layer) — parsed by Docling
  * 3 scanned PDFs (.pdf, PIL image-only)    — no text layer -> Gemini OCR path

Numbers are deliberately tied to existing docs (e.g. the INR 6,30,00,000 Vertex
SaaS wire in statement-003, the 1,33,00,000 HDFC term-loan EMI, the 9.40% post-
addendum rate, the Apollo 1,42,80,000 receipt) so retrieval can stitch a single
fact across markdown + pdf + xlsx + scanned image.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "demo-corpus/domains/finance/docs"
OUT.mkdir(parents=True, exist_ok=True)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
]


def inr(n: int) -> str:
    """Indian-grouping integer formatting: 63000000 -> '6,30,00,000'."""
    s = str(abs(int(n)))
    if len(s) <= 3:
        grp = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grp = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + grp


def _ascii(t: str) -> str:
    """fpdf2 core fonts are latin-1; fold common unicode punctuation to ASCII."""
    return (
        t.replace("—", "-").replace("–", "-")
        .replace("‘", "'").replace("’", "'")
        .replace("“", '"').replace("”", '"')
        .replace("…", "...").replace("₹", "INR ")
    )


# ---------------------------------------------------------------------------
# 3 spreadsheets
# ---------------------------------------------------------------------------

def _style_header(ws, ncols: int) -> None:
    fill = PatternFill("solid", fgColor="1F4E78")
    white = Font(color="FFFFFF", bold=True, size=11)
    thin = Side(style="thin", color="BBBBBB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c in range(1, ncols + 1):
        cell = ws.cell(row=ws._title_row, column=c)
        cell.fill = fill
        cell.font = white
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in ws.iter_rows(min_row=ws._title_row):
        for cell in row:
            cell.border = border


def xlsx_loan_portfolio() -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Loan Portfolio"
    ws["A1"] = "ACME CORP PVT LTD — Consolidated Loan Portfolio (FY2025, as of 31-Mar-2025)"
    ws["A1"].font = Font(bold=True, size=13)
    ws.merge_cells("A1:K1")
    ws["A2"] = "Prepared by Treasury | CIN U72200DL2008PTC184218 | All amounts in INR"
    ws["A2"].font = Font(italic=True, size=9, color="555555")
    ws.merge_cells("A2:K2")
    headers = [
        "Facility ID", "Lender", "Facility Type", "Sanctioned", "Outstanding",
        "Interest Rate", "Monthly EMI", "Start Date", "Maturity", "Status", "Collateral",
    ]
    ws.append([])
    ws._title_row = 4
    ws.append(headers)
    rows = [
        ["HDFC-TL-2023-0847", "HDFC Bank Ltd", "Term Loan", 500000000, 412000000,
         "9.40%", 13300000, "2023-07-01", "2030-06-30", "Live",
         "Gurugram Sector 18 property + receivables"],
        ["HDFC-WC-2024-1120", "HDFC Bank Ltd", "Working Capital", 200000000, 125000000,
         "9.75%", 0, "2024-11-20", "2025-11-19", "Live", "Current assets (hypothecation)"],
        ["NWC-ICD-2024-031", "NorthWind Capital", "Inter-Corporate Deposit", 80000000, 80000000,
         "11.20%", 0, "2024-09-15", "2025-09-14", "Live", "Unsecured"],
        ["APOLLO-VEND-2025", "Apollo Hospitals", "Vendor Advance", 30000000, 12000000,
         "0.00%", 0, "2025-01-10", "2025-07-10", "Live", "Service contract"],
    ]
    for r in rows:
        ws.append(r)
    ws.append([])
    total = sum(r[4] for r in rows)
    ws.append(["", "", "TOTAL OUTSTANDING", "", total, "", "", "", "", "", ""])
    ws.cell(row=ws.max_row, column=3).font = Font(bold=True)
    ws.cell(row=ws.max_row, column=5).font = Font(bold=True)
    _style_header(ws, len(headers))
    widths = [20, 18, 20, 14, 14, 12, 14, 12, 12, 10, 34]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    p = OUT / "loan-portfolio-acme-fy2025.xlsx"
    wb.save(p)
    return p


def xlsx_credit_limits() -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Credit Limits Q2-2025"
    ws["A1"] = "ACME CORP — Counterparty Credit Limits (Q2 2025 revision, effective 2025-04-01)"
    ws["A1"].font = Font(bold=True, size=13)
    ws.merge_cells("A1:G1")
    ws["A2"] = ("Supersedes treasury-004 (FY25). NOTE: Vertex Industries limit raised "
                "75.0 -> 90.0 Cr after Q1 review.")
    ws["A2"].font = Font(italic=True, size=9, color="555555")
    ws.merge_cells("A2:G2")
    headers = ["Counterparty", "Sector", "Sanctioned Limit (INR Cr)",
               "Utilised (INR Cr)", "Available (INR Cr)", "Internal Rating", "Last Review"]
    ws.append([])
    ws._title_row = 4
    ws.append(headers)
    rows = [
        ["Vertex Industries", "Technology / SaaS", 90.0, 63.0, 27.0, "A", "2025-03-28"],
        ["Apollo Hospitals", "Healthcare", 50.0, 14.3, 35.7, "AA", "2025-03-15"],
        ["Orion Consulting", "Professional Services", 25.0, 2.4, 22.6, "BBB", "2025-02-20"],
        ["Helios Partners", "Investments", 40.0, 8.4, 31.6, "A", "2025-03-01"],
        ["Nimbus Technologies", "Technology", 35.0, 4.8, 30.2, "BBB", "2025-03-10"],
        ["Lakeview Properties", "Real Estate", 15.0, 3.3, 11.7, "BB", "2025-01-30"],
    ]
    for r in rows:
        ws.append(r)
    _style_header(ws, len(headers))
    for i, w in enumerate([22, 24, 22, 18, 18, 14, 14], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    p = OUT / "treasury-007-acme-counterparty-credit-limits-q2-2025.xlsx"
    wb.save(p)
    return p


def xlsx_apr_statement() -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "HDFC Apr 2025"
    ws["A1"] = "HDFC BANK — STATEMENT OF ACCOUNT (spreadsheet export)"
    ws["A1"].font = Font(bold=True, size=13)
    ws.merge_cells("A1:E1")
    meta = [
        ("Account Holder", "Acme Corp Pvt Ltd"),
        ("Account No.", "12345678901234"),
        ("Branch", "Gurugram Sector 18"),
        ("Statement period", "1 Apr 2025 - 30 Apr 2025"),
        ("Opening balance (1 Apr)", "INR 28,42,18,520"),
        ("Closing balance (30 Apr)", "INR 29,48,76,340"),
    ]
    r = 2
    for k, v in meta:
        ws.cell(row=r, column=1, value=k).font = Font(bold=True)
        ws.cell(row=r, column=2, value=v)
        r += 1
    ws.append([])
    ws._title_row = r + 1
    headers = ["Date", "Description", "Debit (INR)", "Credit (INR)", "Balance (INR)"]
    ws.append([])
    ws.append(headers)
    # NOTE: the Q2 Vertex wire is 6,93,00,000 (a 10% subscription uplift over
    # the Q1 6,30,00,000) — deliberately a DIFFERENT amount so the Q1 March
    # wire stays unambiguous when retrieval filters by amount.
    txns = [
        ["2025-04-03", "Loan EMI HDFC term loan (HDFC-TL-2023-0847)", 13300000, 0, 270918520],
        ["2025-04-05", "RTGS from Vertex Industries", 0, 11880000, 282798520],
        ["2025-04-10", "NEFT from Apollo Hospitals", 0, 14280000, 297078520],
        ["2025-04-14", "Wire to Vertex (SaaS subscription Q2 - price uplift)", 69300000, 0, 227778520],
        ["2025-04-18", "RTGS from Orion Consulting", 0, 24212000, 251990520],
        ["2025-04-22", "Payroll", 51240000, 0, 200750520],
        ["2025-04-27", "RTGS from misc customers (batched)", 0, 94125820, 294876340],
    ]
    for t in txns:
        ws.append(t)
    _style_header(ws, len(headers))
    for i, w in enumerate([14, 46, 16, 16, 18], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    p = OUT / "statement-009-acme-hdfc-apr-2025.xlsx"
    wb.save(p)
    return p


# ---------------------------------------------------------------------------
# 3 digital PDFs (text layer -> Docling)
# ---------------------------------------------------------------------------

class Doc(FPDF):
    def header(self) -> None:
        pass

    def h1(self, t: str) -> None:
        self.set_font("Helvetica", "B", 15)
        self.multi_cell(0, 8, _ascii(t), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def label(self, k: str, v: str) -> None:
        self.set_font("Helvetica", "B", 10)
        self.cell(48, 6, _ascii(k), new_x="RIGHT", new_y="TOP")
        self.set_font("Helvetica", "", 10)
        self.multi_cell(0, 6, _ascii(v), new_x="LMARGIN", new_y="NEXT")

    def para(self, t: str, size: int = 10) -> None:
        self.set_font("Helvetica", "", size)
        self.multi_cell(0, 5.5, _ascii(t), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)


def pdf_invoice() -> Path:
    d = Doc()
    d.add_page()
    d.h1("TAX INVOICE — Vertex Industries Pvt Ltd")
    d.para("GSTIN: 27AABCV9921K1ZP   |   PAN: AABCV9921K   |   Bengaluru, Karnataka")
    d.ln(2)
    d.label("Invoice No.", "VTX-2025-0042")
    d.label("Invoice Date", "10 March 2025")
    d.label("Bill To", "Acme Corp Pvt Ltd, Gurugram, Haryana (GSTIN 06AAACA1842B1ZF)")
    d.label("Buyer PO", "ACME-PO-2025-118")
    d.ln(3)
    d.set_font("Helvetica", "B", 10)
    d.cell(95, 7, "Description", border=1, new_x="RIGHT", new_y="TOP")
    d.cell(30, 7, "Qty", border=1, align="C", new_x="RIGHT", new_y="TOP")
    d.cell(45, 7, "Amount (INR)", border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    d.set_font("Helvetica", "", 10)
    line_items = [
        ("Vertex Cloud Platform - Enterprise subscription (Q1 2025)", "1 quarter", "5,86,00,000"),
        ("Premium support + SLA uplift", "1 quarter", "44,00,000"),
    ]
    for desc, qty, amt in line_items:
        d.cell(95, 7, desc, border=1, new_x="RIGHT", new_y="TOP")
        d.cell(30, 7, qty, border=1, align="C", new_x="RIGHT", new_y="TOP")
        d.cell(45, 7, amt, border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    d.set_font("Helvetica", "B", 10)
    d.cell(125, 7, "Total payable (inclusive of GST)", border=1, align="R", new_x="RIGHT", new_y="TOP")
    d.cell(45, 7, "6,30,00,000", border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    d.ln(10)
    d.para("Amount in words: Rupees Six Crore Thirty Lakh only.")
    d.para("Payment terms: Net 0 — wire on receipt. Settled via HDFC wire on "
           "12 March 2025 (UTR HDFC0R52025031200042), debit to Acme HDFC "
           "account 12345678901234. This invoice corresponds to the "
           "'Wire to Vertex (SaaS subscription Q1)' line on the Acme HDFC "
           "March 2025 statement.")
    p = OUT / "invoice-vertex-acme-2025-q1.pdf"
    d.output(str(p))
    return p


def pdf_working_capital() -> Path:
    d = Doc()
    d.add_page()
    d.h1("WORKING CAPITAL FACILITY AGREEMENT")
    d.para("Between HDFC Bank Ltd ('the Bank') and Acme Corp Pvt Ltd ('the Borrower'). "
           "Executed at Gurugram on 20 November 2024.")
    d.ln(1)
    d.label("Facility ID", "HDFC-WC-2024-1120")
    d.label("Facility Type", "Cash credit / working capital (renewable)")
    d.label("Sanctioned Limit", "INR 20,00,00,000 (Twenty Crore)")
    d.label("Interest Rate", "9.75% p.a., floating (Repo + 3.25%), reset quarterly")
    d.label("Tenor", "12 months, renewable on annual review")
    d.label("Security", "First-pari-passu hypothecation of current assets")
    d.label("Borrower CIN", "U72200DL2008PTC184218")
    d.label("Operative Account", "12345678901234 (Gurugram Sector 18 branch)")
    d.ln(2)
    d.para("Covenants: (a) Borrower shall maintain a current ratio of not less than "
           "1.20x; (b) the working-capital facility is in addition to the existing "
           "HDFC term loan HDFC-TL-2023-0847 (sanctioned INR 50,00,00,000 at 9.40% "
           "p.a. post Addendum #2); (c) Borrower shall submit a quarterly stock-and-"
           "receivables statement. Total HDFC exposure to the Borrower shall not "
           "exceed INR 70,00,00,000 across both facilities.")
    d.para("For HDFC Bank Ltd: R. Krishnan, Relationship Manager (Corporate). "
           "For Acme Corp Pvt Ltd: Sanjay Mehta, Director (Finance).")
    p = OUT / "loan-005-acme-hdfc-working-capital-facility.pdf"
    d.output(str(p))
    return p


def pdf_board_resolution() -> Path:
    d = Doc()
    d.add_page()
    d.h1("CERTIFIED TRUE COPY — Board Resolution")
    d.para("Acme Corp Pvt Ltd (CIN U72200DL2008PTC184218). Resolution passed at the "
           "meeting of the Board of Directors held on 18 November 2024 at the "
           "registered office, New Delhi.")
    d.ln(1)
    d.para("RESOLVED THAT the Company do open and operate banking facilities with "
           "HDFC Bank Ltd, Gurugram Sector 18 branch, in respect of current account "
           "no. 12345678901234, and that the working-capital facility HDFC-WC-2024-1120 "
           "of INR 20,00,00,000 be and is hereby accepted.")
    d.para("RESOLVED FURTHER THAT the following be authorised signatories (any two "
           "jointly) for instruments up to INR 5,00,00,000, and the Managing Director "
           "singly above that limit:")
    d.label("Signatory A", "Sanjay Mehta — Director (Finance)")
    d.label("Signatory B", "Priya Iyer — VP, Legal & Compliance")
    d.label("Signatory C", "R. Subramanian — Managing Director")
    d.ln(1)
    d.para("Certified true copy. For Acme Corp Pvt Ltd — Company Secretary, "
           "M. No. A24187.")
    p = OUT / "board-resolution-acme-2024-banking.pdf"
    d.output(str(p))
    return p


# ---------------------------------------------------------------------------
# 3 scanned PDFs (image-only, no text layer -> Gemini OCR escalation)
# ---------------------------------------------------------------------------

def _font(size: int, bold: bool = False):
    for cand in FONT_CANDIDATES:
        if bold and "Bold" not in cand:
            continue
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            continue
    for cand in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _scanned(path: Path, title: str, lines: list[tuple[str, int, bool]]) -> Path:
    """Render lines onto an A4 image (150 DPI) -> image-only PDF (no text layer)."""
    W, H = 1240, 1754
    img = Image.new("RGB", (W, H), "#fcfbf7")
    dr = ImageDraw.Draw(img)
    # form-like border + header rule (so it reads as a scanned form)
    dr.rectangle([28, 28, W - 28, H - 28], outline="#9a9a9a", width=2)
    y = 70
    dr.text((70, y), title, fill="#141414", font=_font(40, bold=True))
    y += 60
    dr.line([70, y, W - 70, y], fill="#777777", width=2)
    y += 24
    for text, size, bold in lines:
        if text == "<rule>":
            dr.line([70, y + 8, W - 70, y + 8], fill="#bbbbbb", width=1)
            y += 22
            continue
        dr.text((70, y), text, fill="#1a1a1a", font=_font(size, bold=bold))
        y += size + 14
    img.save(str(path), "PDF", resolution=150.0)
    return path


def scanned_kyc() -> Path:
    lines = [
        ("CUSTOMER ID VERIFICATION FORM  (Branch copy - scanned)", 26, True),
        ("<rule>", 0, False),
        ("Customer Name        : Aniket Desai", 30, False),
        ("Customer Type        : Individual (NRI - resident India)", 28, False),
        ("Designation          : President, India Operations", 28, False),
        ("Employer             : NorthWind Capital India Pvt Ltd (NBFC)", 28, False),
        ("PAN                  : ALWPD7721F", 30, False),
        ("Passport No.         : Z3382217", 30, False),
        ("Date of Birth        : 14 August 1979", 28, False),
        ("Registered Address   : 12 Koregaon Park, Pune 411001", 28, False),
        ("Mobile               : +91-98200-44178", 28, False),
        ("Linked CIF           : HDFC-RET-CIF-00471902", 28, False),
        ("<rule>", 0, False),
        ("KYC Status           : VERIFIED   |   Risk: MEDIUM", 28, True),
        ("Verified by          : S. Rao, Branch Officer", 26, False),
        ("Verification Date    : 11 February 2025", 26, False),
        ("Branch               : HDFC Gurugram Sector 18", 26, False),
    ]
    return _scanned(OUT / "kyc-006-aniket-desai-id-verification-scanned.pdf",
                    "HDFC BANK  -  KYC", lines)


def scanned_wire() -> Path:
    lines = [
        ("OUTWARD WIRE TRANSFER CONFIRMATION (scanned slip)", 26, True),
        ("<rule>", 0, False),
        ("Remitter             : Acme Corp Pvt Ltd", 30, False),
        ("Remitter Account     : 12345678901234 (Gurugram Sector 18)", 26, False),
        ("Beneficiary          : Vertex Industries Pvt Ltd", 30, False),
        ("Beneficiary Bank     : ICICI Bank, Bengaluru", 28, False),
        ("Purpose              : SaaS subscription Q1 - Invoice VTX-2025-0042", 26, False),
        ("Amount               : INR 6,30,00,000", 32, True),
        ("Value Date           : 12 March 2025", 28, False),
        ("UTR / Reference      : HDFC0R52025031200042", 28, False),
        ("Mode                 : RTGS (above INR 2,00,000)", 26, False),
        ("<rule>", 0, False),
        ("Status               : PROCESSED - debit confirmed", 28, True),
        ("Authorised by        : Treasury Maker/Checker (Acme)", 26, False),
    ]
    return _scanned(OUT / "wire-confirmation-acme-vertex-scanned.pdf",
                    "HDFC BANK  -  RTGS", lines)


def scanned_deposit() -> Path:
    lines = [
        ("PAYMENT ADVICE / DEPOSIT SLIP (scanned)", 26, True),
        ("<rule>", 0, False),
        ("Credit To            : Acme Corp Pvt Ltd", 30, False),
        ("Credit Account       : 12345678901234", 28, False),
        ("Remitter             : Apollo Hospitals Enterprise Ltd", 28, False),
        ("Instrument           : NEFT", 28, False),
        ("Narration            : Service contract settlement - Mar 2025", 26, False),
        ("Amount               : INR 1,42,80,000", 32, True),
        ("Value Date           : 14 March 2025", 28, False),
        ("Reference No.        : APOLLO/NEFT/2025/03/8841", 26, False),
        ("<rule>", 0, False),
        ("Status               : CREDITED", 28, True),
        ("Branch Stamp         : HDFC Gurugram Sector 18", 26, False),
    ]
    return _scanned(OUT / "deposit-slip-apollo-acme-scanned.pdf",
                    "HDFC BANK  -  CREDIT ADVICE", lines)


def main() -> None:
    builders = [
        ("xlsx", xlsx_loan_portfolio),
        ("xlsx", xlsx_credit_limits),
        ("xlsx", xlsx_apr_statement),
        ("digital-pdf", pdf_invoice),
        ("digital-pdf", pdf_working_capital),
        ("digital-pdf", pdf_board_resolution),
        ("scanned-pdf", scanned_kyc),
        ("scanned-pdf", scanned_wire),
        ("scanned-pdf", scanned_deposit),
    ]
    print(f"Writing fixtures to {OUT}")
    for kind, fn in builders:
        p = fn()
        print(f"  [{kind:11s}] {p.name:55s} {p.stat().st_size:>7d} bytes")


if __name__ == "__main__":
    main()
