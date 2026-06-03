import logging
import random
import shutil
from datetime import datetime
from pathlib import Path

from database.mongo_client import claims_collection
from database.govt_repository import get_employee_by_mobile
from database.hospital_repository import get_hospital_by_name
from database.user_repository import get_user_by_mobile
from services.letter_service import generate_letter, render_letter_to_pdf
from services.template_letter_service import convert_docx_to_pdf, generate_letter_from_template

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "letter_templates" / "medical_claim_template.pdf"
)
GENERATED_LETTER_DIR = Path(__file__).resolve().parent.parent / "data" / "generated_letters"


def _normalize_letter_type(action_type):
    legacy_map = {
        "approval": "officer_to_beneficiary",
        "rejection": "officer_to_beneficiary",
        "beneficiary_to_officer": "beneficiary_to_officer",
        "officer_to_beneficiary": "officer_to_beneficiary",
        "officer_to_hospital": "officer_to_hospital",
    }
    return legacy_map.get(action_type, "officer_to_beneficiary")


def _format_amount(amount):
    if amount in (None, ""):
        return ""
    try:
        return f"{float(amount):,.2f}"
    except Exception:
        return str(amount)


def _letter_reference(claim):
    existing = claim.get("letter_reference") or claim.get("claim_reference")
    if existing:
        return existing
    year = datetime.now().year
    return f"TNMED-{year}-{random.randint(100000, 999999)}"


def _persist_claim_letter(claim, letter_type, pdf_path, reference):
    from services.storage_service import upload_file
    from config.settings import Config
    claim_id = claim.get("claim_id")
    if not claim_id:
        return str(pdf_path)

    safe_type = str(letter_type).replace("/", "_")
    filename = f"{claim_id}_{safe_type}_{reference}.pdf"
    
    # Upload to Supabase
    public_url = upload_file(str(pdf_path), filename=filename, bucket_name=Config.SUPABASE_LETTER_BUCKET)
    
    generated_at = datetime.utcnow().isoformat() + "Z"
    
    update_data = {
        "letter_reference": reference,
        "claim_reference": reference,
        "letter_generated_at": generated_at,
        f"generated_letters.{safe_type}.generated_at": generated_at,
        f"generated_letters.{safe_type}.reference": reference,
    }
    
    if public_url:
        update_data[f"generated_letters.{safe_type}.url"] = public_url
        update_data[f"generated_letters.{safe_type}.path"] = public_url
    
    claims_collection.update_one(
        {"claim_id": claim_id},
        {"$set": update_data}
    )
    
    return public_url or str(pdf_path)


def _resolve_employee_id(claim):
    claim = claim or {}
    employee_id = claim.get("employee_id")
    if employee_id:
        return employee_id

    mobile = claim.get("mobile")
    if mobile:
        user_doc = get_user_by_mobile(mobile) or {}
        if user_doc.get("employee_id"):
            return user_doc.get("employee_id")

        employee_doc = get_employee_by_mobile(mobile) or {}
        if employee_doc.get("employee_id"):
            return employee_doc.get("employee_id")

    return ""


def _resolve_hospital_name(claim):
    claim = claim or {}
    hospital_name = claim.get("hospital", "")
    if not hospital_name:
        return ""

    hospital_doc = get_hospital_by_name(hospital_name) or {}
    return hospital_doc.get("name") or hospital_name


def _build_template_data(claim, letter_type, letter_body):
    claim = claim or {}
    status = str(claim.get("status", "") or "").strip().title()
    stamp_name = None
    if status == "Approved":
        stamp_name = "verified.png"
    elif status == "Rejected":
        stamp_name = "declined.png"

    reference = claim.get("letter_reference") or claim.get("claim_reference") or _letter_reference(claim)
    rag_result = claim.get("rag_result") or {}
    return {
        "current_date": datetime.now().strftime("%B %d, %Y"),
        "claim_id": claim.get("claim_id", ""),
        "claim_reference": reference,
        "letter_reference": reference,
        "beneficiary_name": claim.get("name", ""),
        "employee_id": _resolve_employee_id(claim),
        "hospital": _resolve_hospital_name(claim),
        "amount": _format_amount(claim.get("amount")),
        "sanctioned_amount": _format_amount(claim.get("sanctioned_amount") or claim.get("amount")),
        "status": claim.get("status", ""),
        "officer_name": claim.get("officer_name", "Claims Officer"),
        "officer_designation": claim.get("officer_designation", "Claims Officer"),
        "officer_notes": claim.get("officer_notes") or claim.get("officer_comments") or "",
        "officer_note": claim.get("officer_note") or "",
        "rag_explanation": rag_result.get("llm_explanation", ""),
        "matched_rule": rag_result.get("matched_rule", ""),
        "source_document": rag_result.get("source_document", ""),
        "letter_body": letter_body,
        "letter_type": letter_type,
        "stamp_path": str(Path(__file__).resolve().parent.parent / "resources" / "stamps" / stamp_name) if stamp_name else "",
    }


def _template_available(template_path):
    try:
        return Path(template_path).exists() and Path(template_path).stat().st_size > 0
    except Exception:
        return False


def generate_pdf_letter(claim, action_type="approval"):
    claim = dict(claim or {})
    letter_type = _normalize_letter_type(action_type)
    reference = _letter_reference(claim)
    claim["letter_reference"] = reference
    if action_type == "approval":
        claim["status"] = "Approved"
    elif action_type == "rejection":
        claim["status"] = "Rejected"
    letter_body = generate_letter(
        letter_type=letter_type,
        claim=claim,
        employee=None,
        hospital=None,
        ai_result=claim.get("ai_result"),
        trust_result=claim.get("trust_result"),
        officer_notes=claim.get("officer_notes") or claim.get("officer_comments"),
    )

    pdf_path = render_letter_to_pdf(letter_body, claim, letter_type=letter_type)

    if _template_available(DEFAULT_TEMPLATE_PATH):
        try:
            import fitz
            import tempfile
            import os
            
            template_doc = fitz.open(DEFAULT_TEMPLATE_PATH)
            content_doc = fitz.open(pdf_path)
            
            for page_index in range(len(content_doc)):
                if page_index < len(template_doc):
                    template_page = template_doc[page_index]
                else:
                    template_page = template_doc.new_page(width=template_doc[0].rect.width, height=template_doc[0].rect.height)
                
                content_page = content_doc[page_index]
                template_page.show_pdf_page(template_page.rect, content_doc, page_index)
            
            output = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            final_pdf_path = output.name
            output.close()
            
            template_doc.save(final_pdf_path)
            template_doc.close()
            content_doc.close()
            
            try:
                os.remove(pdf_path)
            except Exception:
                pass
                
            return _persist_claim_letter(claim, letter_type, final_pdf_path, reference)
        except Exception as exc:
            logger.warning("[Letter] PyMuPDF overlay failed, using plain PDF: %s", exc)

    return _persist_claim_letter(claim, letter_type, pdf_path, reference)
