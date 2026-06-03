import os
import tempfile
import uuid
from datetime import datetime, timezone

from database.mongo_client import claims_collection
from database.hospital_repository import verify_hospital
from services.fraud_service import detect_fraud as detect_fraud_engine
from services.ocr_service import extract_text_advanced
from services.rag_service import rag_validate, retrieve_rules
from services.storage_service import upload_file as upload_to_storage
from services.trust_service import calculate_trust_score
from utils.security_utils import validate_file_upload
from utils.status_utils import normalize_claim_status
from utils.logger import log_claim_state


class ClaimProcessingService:
    def __init__(self, mobile, form_data, uploaded_file):
        self.mobile = mobile
        self.form_data = form_data
        self.uploaded_file = uploaded_file
        self.temp_path = None
        self.claim_id = str(uuid.uuid4())
        self.context = {}

    def validate_upload(self):
        is_valid, error, sanitized_filename = validate_file_upload(self.uploaded_file)
        if not is_valid:
            return False, error

        temp_dir = tempfile.gettempdir()
        self.temp_path = os.path.join(temp_dir, sanitized_filename)
        self.uploaded_file.save(self.temp_path)
        return True, None

    def upload_document(self):
        return upload_to_storage(self.temp_path)

    def run_ocr(self):
        return extract_text_advanced(self.temp_path)

    def extract_entities(self, text):
        from services.entity_extractor import extract_entities
        return extract_entities(text)

    def detect_fraud(self, amount, hospital, extracted_text):
        return detect_fraud_engine(self.mobile, amount, hospital, self.temp_path, extracted_text)

    def validate_claim(self, hospital, extracted_text, entities):
        hospital_check = verify_hospital(hospital)
        ai_result = rag_validate(extracted_text, entities=entities)
        if not isinstance(ai_result, dict):
            ai_result = {
                "eligibility": "Unknown",
                "confidence": 0.0,
                "risk_level": "High",
                "fraud_score": 0.5,
                "hospital_verified": False,
                "reasoning": "AI validation returned an unexpected response.",
                "recommended_action": "Review",
                "fraud_flags": [],
                "missing_documents": [],
                "amount_analysis": {"claimed": 0, "expected_range": "N/A", "status": "anomalous"},
            }
        ai_result.setdefault("fraud_flags", [])
        return hospital_check, ai_result

    def verify_government_rules(self, extracted_text, entities):
        entities = entities or {}
        query_parts = [
            entities.get("surgery"),
            entities.get("procedure"),
            entities.get("diagnosis"),
            entities.get("treatment"),
            entities.get("summary"),
            extracted_text[:800] if extracted_text else "",
        ]
        query = " ".join(str(part) for part in query_parts if part).strip()
        matches = retrieve_rules(query or extracted_text[:800], k=5) if extracted_text or query else []
        top_match = matches[0] if matches else {}
        similarity = round(float(top_match.get("confidence", 0) or 0) * 100, 1)
        eligible = similarity >= 62
        conditional = 48 <= similarity < 62
        status = "Eligible" if eligible else "Conditional" if conditional else "Not Eligible"
        explanation = (
            f"The submitted claim details were compared with Annexure I and Annexure IA. "
            f"The closest government rule match is from {top_match.get('source_document', 'the annexure rules')} "
            f"with {similarity}% similarity. The claim is marked {status.lower()} for officer verification."
            if top_match
            else "No close reimbursement rule match was found in Annexure I or Annexure IA. The claim is marked not eligible pending manual review."
        )
        return {
            "eligible": eligible,
            "status": status,
            "source_document": top_match.get("source_document", ""),
            "matched_rule": top_match.get("matched_rule", ""),
            "similarity_score": similarity,
            "llm_explanation": explanation,
            "matches": matches,
        }

    def calculate_trust(self, hospital, ai_confidence, ocr_confidence, image_hash):
        return calculate_trust_score(
            mobile=self.mobile,
            hospital_name=hospital,
            ai_confidence=ai_confidence,
            ocr_confidence=ocr_confidence,
            image_hash=image_hash,
            duplicate_result=self.context.get("duplicate_result"),
        )

    def persist_claim(self, payload):
        now = datetime.now(timezone.utc).isoformat()
        claim_doc = {
            "claim_id": self.claim_id,
            "mobile": self.mobile,
            "name": payload["name"],
            "hospital": payload["hospital"],
            "amount": float(payload["amount"]),
            "bill_url": payload["bill_url"],
            "extracted_text": payload["extracted_text"],
            "entities": payload["entities"],
            "image_hash": payload["image_hash"],
            "duplicate_hash": payload.get("duplicate_hash"),
            "duplicate_result": payload.get("duplicate_result", {}),
            "ai_result": payload["ai_result"],
            "fraud_result": payload["fraud_result"],
            "trust_result": payload["trust_result"],
            "rag_result": payload.get("rag_result", {}),
            "officer_note": payload.get("officer_note", ""),
            "citizen_remarks_submitted_at": payload.get("citizen_remarks_submitted_at"),
            "status": normalize_claim_status(payload.get("status", "Pending")),
            "confidence_score": payload.get("confidence_score", 0.0),
            "ocr_confidence": payload.get("ocr_confidence", 0.0),
            "ocr_confidence_score": payload.get("ocr_confidence", 0.0),
            "trust_score": payload.get("trust_score", payload.get("trust_result", {}).get("score", 0.0)),
            "trust_level": payload.get("trust_level", payload.get("trust_result", {}).get("level", "LOW")),
            "fraud_level": payload.get("fraud_level", payload.get("fraud_result", {}).get("fraud_level", "LOW")),
            "processing_status": payload.get("processing_status", "completed"),
            "date": now,
            "created_at": now,
            "updated_at": now,
            "ocr_page_count": payload.get("ocr_page_count", 0),
        }
        claims_collection.insert_one(claim_doc)
        log_claim_state(self.claim_id, "None", claim_doc["status"], self.mobile, "Claim submitted")
        return claim_doc

    def cleanup(self):
        if self.temp_path and os.path.exists(self.temp_path):
            os.remove(self.temp_path)


def process_claim_job(claim_id):
    """
    RQ/Celery-compatible placeholder for asynchronous claim enrichment.
    The synchronous path remains the default for backward compatibility.
    """
    claim = claims_collection.find_one({"claim_id": claim_id})
    if not claim:
        return {"ok": False, "reason": "claim_not_found"}
    claims_collection.update_one(
        {"claim_id": claim_id},
        {"$set": {"processing_status": "completed", "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True, "claim_id": claim_id}
