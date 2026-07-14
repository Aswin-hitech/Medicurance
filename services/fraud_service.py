import imagehash
from PIL import Image
import os
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import logging

from config.settings import Config
from database.mongo_client import claims_collection, users_collection, govt_collection
from services.duplicate_detection_service import analyze_duplicate_claim, calculate_file_hash

logger = logging.getLogger(__name__)

def calculate_image_hash(file_path):
    try:
        if file_path.lower().endswith('.pdf'):
            # For PDFs, hash the first page image
            from pdf2image import convert_from_path
            images = convert_from_path(
                file_path,
                first_page=1,
                last_page=1,
                dpi=72,
                poppler_path=Config.POPPLER_PATH,
            )
            if images:
                return str(imagehash.phash(images[0]))
        else:
            return str(imagehash.phash(Image.open(file_path)))
    except Exception as e:
        message = str(e).lower()
        if "poppler" in message or "page count" in message:
            logger.warning("Poppler unavailable")
            return None
        logger.error(f"Error hashing image: {e}")
    return None

def similar(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

def detect_fraud(mobile, amount, hospital, file_path, extracted_text):
    fraud_flags = []
    risk_score = 0  # 0 to 100
    
    current_hash = calculate_image_hash(file_path)
    file_hash = calculate_file_hash(file_path)
    duplicate_result = analyze_duplicate_claim(
        mobile=mobile,
        amount=amount,
        hospital=hospital,
        extracted_text=extracted_text,
        file_hash=file_hash,
        image_hash=current_hash,
    )
    
    # 1. Image Hash Comparison (Duplicate Bill Detection)
    if current_hash:
        duplicate = claims_collection.find_one({"image_hash": current_hash})
        if duplicate:
            fraud_flags.append("Exact duplicate bill image detected.")
            risk_score += Config.FRAUD_WEIGHTS["duplicate"]

    if duplicate_result["duplicate_probability"] >= 70:
        fraud_flags.append("High duplicate probability from multi-factor duplicate analysis.")
        risk_score += 35
    elif duplicate_result["duplicate_probability"] >= 35:
        fraud_flags.append("Moderate duplicate indicators detected.")
        risk_score += 15

    # 2. OCR Similarity Detection
    recent_claims = claims_collection.find({"mobile": mobile}).sort("created_at", -1).limit(5)
    for rc in recent_claims:
        if rc.get("extracted_text"):
            sim = similar(extracted_text, rc["extracted_text"])
            if sim > 0.85:
                fraud_flags.append(f"High text similarity ({int(sim*100)}%) with previous claim {str(rc.get('claim_id') or '')[-8:]}.")
                risk_score += Config.FRAUD_WEIGHTS["similarity"]
                break

    # 3. Same Amount Anomaly
    try:
        amount_float = float(amount)
        same_amount = claims_collection.find_one({
            "mobile": mobile, 
            "amount": amount_float
        })
        if same_amount:
            fraud_flags.append("Identical claim amount submitted previously.")
            risk_score += 20
    except (ValueError, TypeError):
        pass
        
    # 4. Repeated User Claims Abuse
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent_count = claims_collection.count_documents({
        "mobile": mobile,
        "created_at": {"$gte": thirty_days_ago.isoformat()}
    })
    if recent_count >= 3:
        fraud_flags.append(f"High submission frequency: {recent_count} claims in 30 days.")
        risk_score += Config.FRAUD_WEIGHTS["repeated_claim"]
        
    # 5. Repeated Hospital Abuse
    hosp_count = claims_collection.count_documents({
        "hospital": hospital,
        "created_at": {"$gte": thirty_days_ago.isoformat()}
    })
    if hosp_count > 50:
        fraud_flags.append("Hospital flagged for unusually high claim volume.")
        risk_score += 15

    # 6. Government Employee Verification Mismatch
    # Beneficiaries are stored in govtlist — check there first.
    phone_digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    user_doc = (
        govt_collection.find_one({"$or": [{"auth.phone": phone_digits}, {"mobile": phone_digits}, {"phone": phone_digits}]})
        or users_collection.find_one({"mobile": phone_digits})
    )
    if not user_doc:
        fraud_flags.append("Claimant is not a verified government employee.")
        risk_score += 40

    # Cap risk score
    risk_score = min(risk_score, 100)
    
    # Determine Level
    if risk_score >= 70:
        risk_level = "HIGH"
    elif risk_score >= 40:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"
        
    return {
        "fraud_score": round(risk_score / 100.0, 2), # Normalize to 0-1
        "fraud_level": risk_level,
        "fraud_flags": fraud_flags,
        "image_hash": current_hash,
        "duplicate_hash": file_hash,
        "duplicate_result": duplicate_result,
    }
