"""
services/trust_service.py
Phase 7 — Trust Score Engine
"""
import logging
from database.mongo_client import claims_collection, users_collection, govt_collection
from database.hospital_repository import verify_hospital as repo_verify_hospital

logger = logging.getLogger(__name__)

def verify_hospital(hospital_name: str) -> dict:
    hospital_check = repo_verify_hospital(hospital_name)
    return {
        "exists": bool(hospital_check.get("exists") or hospital_check.get("verified")),
        "verified": bool(hospital_check.get("exists") or hospital_check.get("verified")),
        "network": bool((hospital_check.get("exists") or hospital_check.get("verified")) and hospital_check.get("network")),
    }

def _bounded_score(value):
    return min(max(float(value or 0), 0.0), 100.0)


def calculate_trust_score(mobile: str, hospital_name: str, ai_confidence: float, ocr_confidence: float, image_hash: str, duplicate_result: dict = None) -> dict:
    """
    Calculates the multi-dimensional trust score for a claim.
    Returns: { "score": float (0-100), "level": str, "reasoning": list }
    """
    reasoning = []
    
    # 1. AI Confidence (0-100)
    ai_conf_score = min(max(ai_confidence * 100 if ai_confidence <= 1.0 else ai_confidence, 0.0), 100.0)
    
    # 2. OCR Confidence (0-100)
    ocr_conf_score = min(max(ocr_confidence * 100 if ocr_confidence <= 1.0 else ocr_confidence, 0.0), 100.0)
    
    # 3. Hospital Trust (0-100)
    hospital_check = verify_hospital(hospital_name)
    if hospital_check.get("network"):
        hospital_trust = 95.0
        reasoning.append("Treatment taken at In-Network hospital. Facility credentialed.")
    else:
        hospital_trust = 55.0
        reasoning.append("Treatment taken at Out-of-Network hospital. Facility requires manual verification.")
        
    # 4. Duplicate Probability Inverse (0-100)
    duplicate_result = duplicate_result or {}
    duplicate_probability = _bounded_score(duplicate_result.get("duplicate_probability"))
    if not duplicate_result and image_hash:
        dup_count = claims_collection.count_documents({"image_hash": image_hash})
        duplicate_probability = 0.0 if dup_count <= 1 else 85.0
    duplicate_prob_inverse = round(100.0 - duplicate_probability, 1)
    if duplicate_probability >= 70:
        reasoning.append("High duplicate probability based on document, invoice, date, amount, or OCR evidence.")
    elif duplicate_probability >= 35:
        reasoning.append("Some duplicate indicators found; manual review recommended.")
    else:
        reasoning.append("No strong duplicate indicators found.")
        
    # 5. Government Employee Verification (0-100)
    # Beneficiaries live in govtlist (govt_collection), NOT in users collection.
    # Check govtlist first by auth.phone or mobile, fall back to users.
    phone_digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    user_doc = (
        govt_collection.find_one({"$or": [{"auth.phone": phone_digits}, {"mobile": phone_digits}, {"phone": phone_digits}]})
        or users_collection.find_one({"mobile": phone_digits})
    )
    is_govt_verified = bool(user_doc)
    if is_govt_verified:
        govt_verified = 100.0
        reasoning.append("Claimant profile successfully verified against official Government Employee Database.")
    else:
        govt_verified = 40.0
        reasoning.append("Claimant profile unverified in Government Employee list; proceed with caution.")

    weights = {
        "ai_confidence": 0.30,
        "ocr_confidence": 0.20,
        "hospital_trust": 0.20,
        "duplicate_prob_inverse": 0.20,
        "govt_verified": 0.10,
    }
    components = {
        "ai_confidence": ai_conf_score,
        "ocr_confidence": ocr_conf_score,
        "hospital_trust": hospital_trust,
        "duplicate_prob_inverse": duplicate_prob_inverse,
        "govt_verified": govt_verified,
    }
    contributions = {
        key: round(components[key] * weight, 1)
        for key, weight in weights.items()
    }
    trust_score = round(sum(contributions.values()), 1)

    # Determine Level
    if trust_score >= 85:
        level = "HIGH"
    elif trust_score >= 60:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "score": trust_score,
        "level": level,
        "reasoning": reasoning,
        "components": components,
        "weights": weights,
        "contributions": contributions,
        "duplicate_probability": duplicate_probability,
    }
