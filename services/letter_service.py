import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fpdf import FPDF

logger = logging.getLogger(__name__)


def _safe_text(value):
    return "" if value in (None, "") else str(value).strip()


def _format_amount(amount):
    if amount in (None, ""):
        return "Rs. 0.00"
    try:
        value = Decimal(str(amount))
        return f"Rs. {value:,.2f}"
    except (InvalidOperation, ValueError):
        return f"Rs. {_safe_text(amount)}"


def _extract_entities(claim):
    entities = claim.get("entities") or {}
    return {
        "diagnosis": _safe_text(entities.get("diagnosis") or entities.get("treatment") or claim.get("diagnosis")),
        "admission_date": _safe_text(entities.get("admission_date") or claim.get("admission_date")),
        "discharge_date": _safe_text(entities.get("discharge_date") or claim.get("discharge_date")),
        "doctor_name": _safe_text(entities.get("doctor_name") or claim.get("doctor_name")),
        "invoice_number": _safe_text(entities.get("invoice_number") or claim.get("invoice_number")),
        "summary": _safe_text(entities.get("summary") or claim.get("treatment_summary") or ""),
    }


def _build_beneficiary_letter(claim):
    entities = _extract_entities(claim)
    hospital = _safe_text(claim.get("hospital"))
    amount = _format_amount(claim.get("amount"))
    return "\n\n".join([
        f"Date: {datetime.now().strftime('%d %B %Y')}",
        f"Subject: Request for reimbursement approval for Claim #{_safe_text(claim.get('claim_id'))[-8:]}",
        "Respected Sir/Madam,",
        (
            f"I respectfully submit this request for reimbursement of medical expenses incurred for treatment "
            f"at {hospital}. The treatment summary is noted as {entities['diagnosis'] or 'medical treatment'}."
            + (f" Admission Date: {entities['admission_date']}." if entities["admission_date"] else "")
            + (f" Discharge Date: {entities['discharge_date']}." if entities["discharge_date"] else "")
            + (f" Doctor: {entities['doctor_name']}." if entities["doctor_name"] else "")
        ).strip(),
        (
            f"The claimed amount is {amount}. Kindly consider this request and approve reimbursement at the earliest "
            f"in accordance with the applicable rules and records."
        ),
        "Thank you for your kind consideration.",
        "Yours faithfully,",
        _safe_text(claim.get("name")) or "Beneficiary",
        f"PPO Number: {_safe_text(claim.get('ppo_number') or claim.get('employee_id')) or 'N/A'}",
    ])


def _build_officer_letter(claim):
    status = str(claim.get("status") or "Escalated").strip().title()
    officer_name = _safe_text(claim.get("officer_name")) or "Claims Officer"
    officer_designation = _safe_text(claim.get("officer_designation")) or "Claims Officer"
    notes = _safe_text(claim.get("officer_notes") or claim.get("officer_comments") or "No additional remarks recorded.")
    citizen_note = _safe_text(claim.get("officer_note"))
    rag_result = claim.get("rag_result") or {}
    ai_result = claim.get("ai_result") or {}
    reference = _safe_text(claim.get("letter_reference") or claim.get("claim_reference"))
    amount = _format_amount(claim.get("amount"))
    sanctioned = _format_amount(claim.get("sanctioned_amount") or claim.get("amount"))
    matched_rule = _safe_text(rag_result.get("matched_rule"))
    rag_explanation = _safe_text(rag_result.get("llm_explanation"))
    missing_documents = ai_result.get("missing_documents") or []

    if status == "Approved":
        decision_blocks = [
            "The medical reimbursement claim has been approved after verification of the submitted bill, hospital details, beneficiary eligibility, OCR extraction, duplicate checks, trust assessment, and applicable government reimbursement rules.",
            f"Claim Amount: {amount}. Sanctioned Amount: {sanctioned}. The sanctioned amount will be processed subject to treasury/payment workflow and any applicable departmental checks.",
            "Next Steps: The beneficiary may retain this letter for records. Payment processing will continue through the authorised reimbursement channel. For clarifications, contact the claims office with the reference number mentioned above.",
        ]
    elif status == "Rejected":
        missing_text = ", ".join(str(item) for item in missing_documents if item) or "supporting documents or eligibility conditions were insufficient for approval"
        decision_blocks = [
            "The medical reimbursement claim has been rejected after official review of the submitted records and rule verification.",
            f"Primary Reason: {notes}. Missing/insufficient details: {missing_text}.",
            "Appeal Instructions: The beneficiary may submit an appeal or revised claim with additional documents, corrected bills, discharge summaries, prescriptions, or other supporting records through the concerned office.",
        ]
    else:
        decision_blocks = [
            "The medical reimbursement claim has been reviewed and referred for further official examination.",
            f"Officer Remarks: {notes}.",
            "The beneficiary will be informed after completion of additional verification.",
        ]

    return "\n\n".join([
        f"Date: {datetime.now().strftime('%d %B %Y')}",
        "Government of Tamil Nadu",
        "Medical Reimbursement Claims Cell",
        f"Reference Number: {reference or 'To be generated'}",
        f"Subject: Official communication regarding medical reimbursement claim #{_safe_text(claim.get('claim_id'))[-8:]}",
        "Respected Sir/Madam,",
        f"Beneficiary: {_safe_text(claim.get('name')) or 'Registered beneficiary'}",
        f"Hospital: {_safe_text(claim.get('hospital')) or 'Not specified'}",
        f"Claim Status: {status}",
        *decision_blocks,
        f"Government Rule Verification: {rag_explanation or 'The claim has been reviewed against available reimbursement guidance.'}",
        f"Policy Reference: {matched_rule[:500] if matched_rule else 'Annexure I / Annexure IA rule verification recorded in the claim file.'}",
        (
            "Additional Information Submitted by Claimant: "
            + (citizen_note if citizen_note else "No additional claimant remarks were submitted.")
        ),
        f"Official Remarks: {notes}",
        "This letter is issued for citizen information, departmental records, and necessary action.",
        "Yours faithfully,",
        officer_name,
        officer_designation,
    ])


def _build_hospital_letter(claim):
    hospital = _safe_text(claim.get("hospital"))
    officer_name = _safe_text(claim.get("officer_name")) or "Claims Officer"
    officer_designation = _safe_text(claim.get("officer_designation")) or "Claims Officer"
    claim_id = _safe_text(claim.get("claim_id"))[-8:]
    amount = _format_amount(claim.get("amount"))
    return "\n\n".join([
        f"Date: {datetime.now().strftime('%d %B %Y')}",
        f"Subject: Request for treatment and billing verification for Claim #{claim_id}",
        "To The Medical Superintendent,",
        hospital,
        "Respected Sir/Madam,",
        (
            f"This office requests verification of the treatment, admission details, and billing authenticity "
            f"for the above claim. Kindly confirm the patient admission records, treatment particulars, and "
            f"the billed amount of {amount}."
        ),
        "Your confirmation will assist in timely processing of the reimbursement request.",
        "Yours faithfully,",
        officer_name,
        officer_designation,
    ])


def generate_letter(letter_type, claim, employee, hospital, ai_result, trust_result, officer_notes):
    """
    Build a formal government-style letter body with no AI commentary.
    """
    claim = claim or {}
    normalized_type = str(letter_type or "").strip().lower()
    status = str(claim.get("status") or "").strip().title()
    claim = dict(claim)
    claim["status"] = status
    if officer_notes and not claim.get("officer_notes"):
        claim["officer_notes"] = officer_notes

    if normalized_type == "beneficiary_to_officer":
        return _build_beneficiary_letter(claim)
    if normalized_type == "officer_to_hospital":
        return _build_hospital_letter(claim)
    return _build_officer_letter(claim)


def render_letter_to_pdf(letter_text, claim, letter_type="officer_to_beneficiary"):
    """
    Fallback PDF renderer for formal letters.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    logo_path = root / "static" / "assets" / "app_logo.png"
    status = str(claim.get("status") or "").strip().title()
    stamp_path = root / "resources" / "stamps" / ("verified.png" if status == "Approved" else "declined.png" if status == "Rejected" else "")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    
    # Push content down to avoid overlapping the template's official header
    pdf.set_y(55)

    pdf.set_font("Arial", "B", 11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, str(letter_type).replace("_", " ").title(), 0, 1, "C")
    pdf.ln(4)
    pdf.set_font("Arial", size=11)
    
    def sanitize_for_fpdf(text):
        replacements = {
            '\u2011': '-', '\u2013': '-', '\u2014': '--',
            '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
            '\u2022': '-', '\u2026': '...', '\u00a0': ' '
        }
        for k, v in replacements.items():
            text = text.replace(k, v)
        return text.encode('latin-1', 'replace').decode('latin-1')

    for paragraph in str(letter_text).splitlines():
        if paragraph.strip():
            pdf.multi_cell(0, 7, sanitize_for_fpdf(paragraph))
        else:
            pdf.ln(2)

    pdf.ln(8)
    if stamp_path.exists():
        try:
            pdf.image(str(stamp_path), x=150, y=pdf.get_y(), w=34)
            pdf.ln(20)
        except Exception:
            pass
    pdf.cell(0, 8, "Authorized Signatory", 0, 1)
    pdf.cell(0, 8, "MediCurance Officer", 0, 1)

    import os
    import tempfile

    temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf_path = temp_pdf.name
    temp_pdf.close()
    pdf.output(pdf_path)
    return pdf_path
