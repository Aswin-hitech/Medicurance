import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any

from agents.orchestrator_agent import get_orchestrator_agent
from database.mongo_client import claims_collection
from services.storage_service import upload_file as upload_to_storage
from memory.claim_memory import ClaimMemory
from utils.security_utils import validate_file_upload
from utils.status_utils import normalize_claim_status
from utils.logger import log_claim_state, logger


class ClaimProcessingService:
    def __init__(self, mobile, form_data, uploaded_files, claim_id=None):
        self.mobile = mobile
        self.form_data = form_data
        self.uploaded_files = uploaded_files if isinstance(uploaded_files, list) else [uploaded_files]
        self.temp_paths = []
        self.claim_id = claim_id or str(uuid.uuid4())
        self.context = {}
        self.orchestrator = get_orchestrator_agent()

    def validate_upload(self):
        temp_dir = tempfile.gettempdir()
        for file in self.uploaded_files:
            is_valid, error, sanitized_filename = validate_file_upload(file)
            if not is_valid:
                return False, f"{file.filename}: {error}"
            t_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_{sanitized_filename}")
            file.save(t_path)
            self.temp_paths.append(t_path)
        return True, None

    def upload_document(self):
        urls = []
        for path in self.temp_paths:
            url = upload_to_storage(path)
            if url:
                urls.append(url)
        return urls

    def run_ocr(self):
        from services.ocr_service import extract_text_advanced
        full_text = []
        for path in self.temp_paths:
            text = extract_text_advanced(path)
            if text:
                full_text.append(text)
        return "\n\n".join(full_text)

    def extract_entities(self, text):
        from services.entity_extractor import extract_entities
        return extract_entities(text)

    def detect_fraud(self, amount, hospital, extracted_text):
        from services.fraud_service import detect_fraud as detect_fraud_engine
        
        # Fraud detection currently checks the first image for visual tampering, or we can pass all. 
        # For now, pass the first image.
        primary_path = self.temp_paths[0] if self.temp_paths else None
        return detect_fraud_engine(self.mobile, amount, hospital, primary_path, extracted_text)

    def validate_claim(self, hospital, extracted_text, entities):
        from database.hospital_repository import verify_hospital
        from services.rag_service import rag_validate

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
        from services.rag_service import retrieve_rules

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
        from services.trust_service import calculate_trust_score

        return calculate_trust_score(
            mobile=self.mobile,
            hospital_name=hospital,
            ai_confidence=ai_confidence,
            ocr_confidence=ocr_confidence,
            image_hash=image_hash,
            duplicate_result=self.context.get("duplicate_result"),
        )

    def build_memory(self, bill_urls: list = None) -> ClaimMemory:
        amount_value = self.form_data.get("amount", 0)
        try:
            amount = float(amount_value or 0)
        except (TypeError, ValueError):
            amount = 0.0
        officer_note = str(self.form_data.get("officer_note", "") or "").strip()
        citizen_remarks_submitted_at = (
            datetime.now(timezone.utc).isoformat() if officer_note else None
        )
        bill_url = bill_urls[0] if bill_urls else ""
        return ClaimMemory(
            claim_id=self.claim_id,
            mobile=self.mobile,
            name=str(self.form_data.get("name", "")).strip(),
            hospital=str(self.form_data.get("hospital", "")).strip(),
            amount=amount,
            bill_url=bill_url,
            temp_path=self.temp_paths[0] if self.temp_paths else "",
            form_data={
                "officer_note": officer_note,
                "citizen_remarks_submitted_at": citizen_remarks_submitted_at,
                "bill_urls": bill_urls or []
            },
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
            "bill_urls": payload.get("form_data", {}).get("bill_urls", [payload.get("bill_url")] if payload.get("bill_url") else []),
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
            "status": "Pending",  # Ensure claims always wait for officer review
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
            "agent_trace": payload.get("agent_trace", []),
            "reflection_notes": payload.get("reflection_notes", []),
            "final_decision": payload.get("final_decision", payload.get("decision", "")),
            "recommendation": payload.get("recommendation", {}),
            "policy_clauses": payload.get("policy_clauses", []),
            "source_references": payload.get("source_references", []),
            "reasoning_summaries": payload.get("reasoning_summaries", {}),
            "intermediate_decisions": payload.get("intermediate_decisions", []),
            "workflow_metadata": payload.get("workflow_metadata", {}),
            "retries": payload.get("retries", {}),
            "errors": payload.get("errors", []),
        }
        claims_collection.update_one(
            {"claim_id": self.claim_id},
            {"$set": claim_doc},
            upsert=True
        )
        log_claim_state(self.claim_id, "None", claim_doc["status"], self.mobile, "Claim submitted or updated")
        return claim_doc

    def process_claim(self):
        is_valid, error = self.validate_upload()
        if not is_valid:
            return {"ok": False, "message": error, "claim_id": self.claim_id}

        try:
            bill_urls = self.upload_document()
            if not bill_urls:
                bill_urls = [p for p in self.temp_paths if p]
            workflow_memory = self.build_memory(bill_urls=bill_urls)
            result_memory = self.orchestrator.execute(workflow_memory)
            claim_doc = self.persist_claim(result_memory.to_claim_document())

            decision = result_memory.decision or claim_doc.get("final_decision") or claim_doc.get("status")
            if decision == "Approve":
                message = "Claim approved by the agentic workflow."
            elif decision == "Reject":
                message = "Claim rejected by the agentic workflow."
            elif decision == "Request Additional Documents":
                message = "Additional documents are required before the claim can proceed."
            else:
                message = "Claim escalated for officer review."

            return {
                "ok": True,
                "claim_id": claim_doc["claim_id"],
                "status": claim_doc["status"],
                "decision": claim_doc.get("final_decision", decision),
                "message": message,
                "claim": claim_doc,
            }
        except Exception as exc:
            logger.exception("[ClaimProcessing] Agentic workflow failed: %s", exc)
            raise ClaimProcessingError(str(exc)) from exc
        finally:
            self.cleanup()

    def cleanup(self):
        for path in self.temp_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


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
