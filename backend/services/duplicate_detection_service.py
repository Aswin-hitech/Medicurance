import hashlib
import re
from difflib import SequenceMatcher

from database.mongo_client import claims_collection


def _normalize_text(value):
    return " ".join(str(value or "").lower().split())


def _similarity(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize_text(a), _normalize_text(b)).ratio()


def _amount_similarity(current, previous):
    try:
        current_amount = float(current)
        previous_amount = float(previous)
    except (TypeError, ValueError):
        return 0.0
    if current_amount <= 0 or previous_amount <= 0:
        return 0.0
    delta = abs(current_amount - previous_amount) / max(current_amount, previous_amount)
    return max(0.0, 1.0 - delta)


def calculate_file_hash(file_path):
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_duplicate_entities(text):
    text = str(text or "")
    invoice_match = re.search(r"(?:invoice|bill|receipt)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-\/]{4,})", text, re.I)
    admission_match = re.search(r"(?:admission|admitted)\s*(?:date)?\s*[:\-]?\s*(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4})", text, re.I)
    discharge_match = re.search(r"(?:discharge|discharged)\s*(?:date)?\s*[:\-]?\s*(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4})", text, re.I)
    return {
        "invoice_number": invoice_match.group(1).strip().upper() if invoice_match else "",
        "admission_date": admission_match.group(1).strip() if admission_match else "",
        "discharge_date": discharge_match.group(1).strip() if discharge_match else "",
    }


def analyze_duplicate_claim(mobile, amount, hospital, extracted_text, file_hash=None, image_hash=None, entities=None):
    """
    Multi-layer duplicate scoring. Scores are capped at 100 and include
    evidence so reviewers can distinguish near matches from hard duplicates.
    """
    entities = {**extract_duplicate_entities(extracted_text), **(entities or {})}
    invoice_number = entities.get("invoice_number") or ""
    admission_date = entities.get("admission_date") or ""
    discharge_date = entities.get("discharge_date") or ""
    current_hospital = _normalize_text(hospital)

    candidates = claims_collection.find({
        "$or": [
            {"mobile": mobile},
            {"duplicate_hash": file_hash} if file_hash else {"_id": None},
            {"image_hash": image_hash} if image_hash else {"_id": None},
            {"entities.invoice_number": invoice_number} if invoice_number else {"_id": None},
        ]
    }).sort("created_at", -1).limit(25)

    best_score = 0.0
    best_explanation = []
    best_claim_id = None

    for claim in candidates:
        score = 0.0
        explanation = []

        if file_hash and claim.get("duplicate_hash") == file_hash:
            score += 35
            explanation.append("Exact file hash match.")
        if image_hash and claim.get("image_hash") == image_hash:
            score += 25
            explanation.append("Perceptual image hash match.")

        previous_entities = claim.get("entities") or {}
        previous_invoice = str(previous_entities.get("invoice_number") or "").upper()
        if invoice_number and invoice_number == previous_invoice:
            score += 20
            explanation.append("Invoice number matches a previous claim.")

        if current_hospital and current_hospital == _normalize_text(claim.get("hospital")):
            score += 10
            explanation.append("Hospital matches.")

        amount_match = _amount_similarity(amount, claim.get("amount"))
        if amount_match >= 0.98:
            score += 10
            explanation.append("Claim amount is effectively identical.")
        elif amount_match >= 0.9:
            score += 5
            explanation.append("Claim amount is very close.")

        previous_admission = previous_entities.get("admission_date")
        previous_discharge = previous_entities.get("discharge_date")
        if admission_date and admission_date == previous_admission:
            score += 8
            explanation.append("Admission date matches.")
        if discharge_date and discharge_date == previous_discharge:
            score += 8
            explanation.append("Discharge date matches.")

        text_similarity = _similarity(extracted_text, claim.get("extracted_text"))
        if text_similarity >= 0.9:
            score += 20
            explanation.append(f"OCR text is {int(text_similarity * 100)}% similar.")
        elif text_similarity >= 0.75:
            score += 10
            explanation.append(f"OCR text is {int(text_similarity * 100)}% similar.")

        score = min(score, 100.0)
        if score > best_score:
            best_score = score
            best_explanation = explanation
            best_claim_id = claim.get("claim_id")

    if not best_explanation:
        best_explanation = ["No strong duplicate indicators found."]

    return {
        "duplicate_probability": round(best_score, 1),
        "matched_claim_id": best_claim_id,
        "explanation": best_explanation,
        "invoice_number": invoice_number,
        "admission_date": admission_date,
        "discharge_date": discharge_date,
    }
