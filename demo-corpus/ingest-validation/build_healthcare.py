"""Build frontmatter-free healthcare PDFs for cross-doc ingest validation.

One patient (Rohan Mehta) at Apollo Hospitals, seen by Dr. Priya Sharma,
across THREE doc-types — a lab report, a discharge summary, and an insurance
EOB. Shared entities (patient / hospital / physician) let us test cross-doc +
cross-type entity resolution; the numeric lab/billing values test typing
(incl. an HbA1c "%"); and because these are real PDFs there is NO frontmatter,
so classification + coverage (FIX 3) run with zero metadata crutch.

    uv run --with reportlab python demo-corpus/ingest-validation/build_healthcare.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer

OUT = Path(__file__).parent
styles = getSampleStyleSheet()
H = ParagraphStyle("H", parent=styles["Title"], fontSize=15, spaceAfter=10)
SUB = ParagraphStyle("SUB", parent=styles["Heading2"], fontSize=11, spaceAfter=6)
BODY = ParagraphStyle("BODY", parent=styles["BodyText"], fontSize=10, leading=14)
MONO = ParagraphStyle("MONO", parent=styles["Code"], fontSize=9, leading=12)


def _build(name: str, flow: list) -> None:
    doc = SimpleDocTemplate(
        str(OUT / name), pagesize=letter,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
    )
    doc.build(flow)
    print(f"  wrote {name}")


def lab_report() -> None:
    flow = [
        Paragraph("APOLLO HOSPITALS — DIAGNOSTIC LABORATORY", H),
        Paragraph("Pune, Maharashtra", BODY),
        Spacer(1, 8),
        Paragraph("LABORATORY REPORT", SUB),
        Paragraph(
            "Patient Name: Rohan Mehta<br/>"
            "Patient ID: AH-PT-88421<br/>"
            "Age / Sex: 47 / Male<br/>"
            "Referring Physician: Dr. Priya Sharma<br/>"
            "Collection Date: 12 May 2026<br/>"
            "Report Date: 13 May 2026", BODY),
        Spacer(1, 10),
        Paragraph("Complete Blood Count &amp; Metabolic Panel", SUB),
        Preformatted(
            "Test                    Result       Reference Range\n"
            "Fasting Glucose         142 mg/dL    70 - 100\n"
            "Total Cholesterol       236 mg/dL    < 200\n"
            "HDL Cholesterol         38 mg/dL     > 40\n"
            "LDL Cholesterol         158 mg/dL    < 130\n"
            "Triglycerides           210 mg/dL    < 150\n"
            "Hemoglobin              13.8 g/dL    13.0 - 17.0\n"
            "HbA1c                   7.8 %        < 5.7", MONO),
        Spacer(1, 10),
        Paragraph(
            "Impression: Elevated fasting glucose and HbA1c consistent with "
            "type 2 diabetes mellitus. Dyslipidemia noted. Clinical "
            "correlation advised.", BODY),
        Spacer(1, 10),
        Paragraph("Dr. Priya Sharma, MD<br/>Apollo Hospitals, Pune", BODY),
    ]
    _build("lab-report-rohan-mehta.pdf", flow)


def discharge_summary() -> None:
    flow = [
        Paragraph("APOLLO HOSPITALS, PUNE", H),
        Paragraph("DISCHARGE SUMMARY", SUB),
        Paragraph(
            "Patient Name: Rohan Mehta<br/>"
            "Patient ID: AH-PT-88421<br/>"
            "Admission Date: 20 May 2026<br/>"
            "Discharge Date: 24 May 2026<br/>"
            "Attending Physician: Dr. Priya Sharma<br/>"
            "Department: Internal Medicine", BODY),
        Spacer(1, 10),
        Paragraph(
            "Diagnosis: Type 2 Diabetes Mellitus with hyperglycemia; "
            "Dyslipidemia.", BODY),
        Spacer(1, 6),
        Paragraph(
            "Hospital Course: The patient, Rohan Mehta, was admitted with "
            "fatigue and elevated blood glucose. He was managed with insulin "
            "and statin therapy under Dr. Priya Sharma. Blood glucose "
            "stabilized to 118 mg/dL by the time of discharge.", BODY),
        Spacer(1, 6),
        Paragraph(
            "Discharge Medications:<br/>"
            "- Metformin 500 mg twice daily<br/>"
            "- Atorvastatin 20 mg once daily", BODY),
        Spacer(1, 6),
        Paragraph(
            "Follow-up: Review with Dr. Priya Sharma in 4 weeks at Apollo "
            "Hospitals, Pune.", BODY),
    ]
    _build("discharge-summary-rohan-mehta.pdf", flow)


def insurance_eob() -> None:
    flow = [
        Paragraph("STAR HEALTH INSURANCE", H),
        Paragraph("EXPLANATION OF BENEFITS (This is not a bill)", SUB),
        Paragraph(
            "Member: Rohan Mehta<br/>"
            "Member ID: SH-MEM-553201<br/>"
            "Policy Number: STAR-GOLD-2026<br/>"
            "Provider: Apollo Hospitals, Pune<br/>"
            "Claim Number: CLM-2026-44120<br/>"
            "Date of Service: 20 May 2026 to 24 May 2026", BODY),
        Spacer(1, 10),
        Preformatted(
            "Billed Amount             Rs. 1,84,500\n"
            "Allowed Amount            Rs. 1,52,000\n"
            "Plan Paid                 Rs. 1,36,800\n"
            "Patient Responsibility    Rs. 15,200", MONO),
        Spacer(1, 10),
        Paragraph(
            "Claim processed for member Rohan Mehta for inpatient treatment "
            "at Apollo Hospitals, Pune. The patient responsibility of "
            "Rs. 15,200 reflects the policy co-payment.", BODY),
    ]
    _build("insurance-eob-rohan-mehta.pdf", flow)


if __name__ == "__main__":
    lab_report()
    discharge_summary()
    insurance_eob()
    print("done.")
