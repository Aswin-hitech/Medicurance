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
        
        # Support dict of lists for attachments, or list of files (backward compatible)
        if isinstance(uploaded_files, dict):
            self.attachments = uploaded_files
        else:
            files_list = uploaded_files if isinstance(uploaded_files, list) else [uploaded_files]
            self.attachments = {"bills": [f for f in files_list if f]}
            
        self.temp_paths = [] # List of temp bill paths for OCR pipeline (backward compatibility)
        self.all_temp_paths = {} # Dict mapping attachment name -> list of temp paths
        self.claim_id = claim_id or str(uuid.uuid4())
        self.context = {}
        self.orchestrator = get_orchestrator_agent()

    def validate_upload(self):
        temp_dir = tempfile.gettempdir()
        
        # 1. Process bills (populates self.temp_paths for backward compatibility)
        self.temp_paths = []
        for file in self.attachments.get("bills", []):
            if not file:
                continue
            is_valid, error, sanitized_filename = validate_file_upload(file)
            if not is_valid:
                return False, f"{file.filename}: {error}"
            t_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_{sanitized_filename}")
            file.save(t_path)
            self.temp_paths.append(t_path)
            
        # 2. Process all attachments
        self.all_temp_paths = {"bills": self.temp_paths}
        for field_name, files in self.attachments.items():
            if field_name == "bills":
                continue
            self.all_temp_paths[field_name] = []
            for file in files:
                if not file:
                    continue
                is_valid, error, sanitized_filename = validate_file_upload(file)
                if not is_valid:
                    return False, f"{file.filename}: {error}"
                t_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_{sanitized_filename}")
                file.save(t_path)
                self.all_temp_paths[field_name].append(t_path)
                
        return True, None

    def upload_document(self):
        urls = {}
        for field_name, paths in self.all_temp_paths.items():
            urls[field_name] = []
            for path in paths:
                folder = "claims"
                if field_name == "passport_photo":
                    folder = "profile-images"
                elif field_name in ["prescriptions", "discharge_summary", "investigation_reports", "certificates"]:
                    folder = "medical-docs"
                elif field_name in ["id_proof", "ppo_proof"]:
                    folder = "identity-docs"
                    
                url = upload_to_storage(path, folder=folder)
                if url:
                    urls[field_name].append(url)
        return urls

    def _fetch_profile_documents(self, uploaded_urls):
        """Fetch pre-uploaded profile photo and documents from user profile in database if missing."""
        try:
            from database.mongo_client import govt_collection, users_collection
            phone_digits = "".join(ch for ch in str(self.mobile or "") if ch.isdigit())
            query = {"$or": [{"auth.phone": phone_digits}, {"mobile": phone_digits}, {"phone": phone_digits}]}
            p_doc = govt_collection.find_one(query) or users_collection.find_one(query) or {}
            docs = p_doc.get("documents") or {}
            
            # Helper to extract URL from a document sub-document (which can be a string or a dict)
            def get_doc_url(doc_field):
                if not doc_field:
                    return None
                if isinstance(doc_field, dict):
                    return doc_field.get("url")
                return str(doc_field)

            # 1. Passport Size Photo
            if not uploaded_urls.get("passport_photo"):
                photo_url = (
                    p_doc.get("profilePhoto") or
                    p_doc.get("profile", {}).get("profilePhoto") or
                    p_doc.get("profile", {}).get("photo") or
                    p_doc.get("profile", {}).get("photo_url") or
                    get_doc_url(docs.get("profilePhoto"))
                )
                if photo_url:
                    uploaded_urls["passport_photo"] = [photo_url]
                    logger.info("[ClaimProcessing] Fetched passport photo from user profile: %s", photo_url)

            # 2. ID Proof (Aadhaar, PAN, Voter ID)
            if not uploaded_urls.get("id_proof"):
                id_url = get_doc_url(docs.get("aadhaar")) or get_doc_url(docs.get("pan")) or get_doc_url(docs.get("voterId"))
                if id_url:
                    uploaded_urls["id_proof"] = [id_url]
                    logger.info("[ClaimProcessing] Fetched ID proof from user profile documents: %s", id_url)

            # 3. PPO Proof (PPO Document or Bank Passbook)
            if not uploaded_urls.get("ppo_proof"):
                ppo_url = get_doc_url(docs.get("ppo")) or get_doc_url(docs.get("bankPassbook"))
                if ppo_url:
                    uploaded_urls["ppo_proof"] = [ppo_url]
                    logger.info("[ClaimProcessing] Fetched PPO proof from user profile documents: %s", ppo_url)

            # 4. Certificates / Digital Life Certificate
            if not uploaded_urls.get("certificates"):
                cert_url = get_doc_url(docs.get("digitalLifeCertificate")) or get_doc_url(docs.get("certificates"))
                if cert_url:
                    uploaded_urls["certificates"] = [cert_url]
                    logger.info("[ClaimProcessing] Fetched certificates from user profile documents: %s", cert_url)

        except Exception as e:
            logger.warning("[ClaimProcessing] Failed to fetch documents from profile: %s", e)

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
        specialty_boost = 0.0

        if top_match:
            match_text = " ".join(
                str(value).lower()
                for value in [
                    entities.get("surgery"),
                    entities.get("procedure"),
                    entities.get("diagnosis"),
                    entities.get("treatment"),
                    entities.get("speciality"),
                    entities.get("specialty"),
                ]
                if value
            )
            rule_text = str(top_match.get("matched_rule", "")).lower()
            if match_text and any(token in rule_text for token in match_text.split()):
                specialty_boost = 12.5
            elif any(keyword in rule_text for keyword in ["specialty", "speciality", "procedure", "surgery", "treatment"]):
                specialty_boost = 8.0

        score = min(100.0, similarity + specialty_boost)
        recommended = score >= 62
        conditional = 48 <= score < 62
        status = "Recommended" if recommended else "Conditional" if conditional else "Not Recommended"
        explanation = (
            f"The submitted claim details were compared with Annexure I and Annexure IA. "
            f"The closest government rule match is from {top_match.get('source_document', 'the annexure rules')} "
            f"with {similarity}% similarity. Specialty alignment contributed a {specialty_boost}% boost, "
            f"bringing the score to {score}%. The claim is marked {status.lower()} for officer verification."
            if top_match
            else "No close reimbursement rule match was found in Annexure I or Annexure IA. The claim is marked not recommended pending manual review."
        )
        return {
            "eligible": recommended,
            "status": status,
            "source_document": top_match.get("source_document", ""),
            "matched_rule": top_match.get("matched_rule", ""),
            "similarity_score": score,
            "base_similarity_score": similarity,
            "specialty_boost": specialty_boost,
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
            "generated_application": payload.get("generated_application"),
            "attachments": payload.get("attachments"),
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

    def update_user_profile_photo(self, photo_url):
        try:
            from database.mongo_client import govt_collection, users_collection
            phone_digits = "".join(ch for ch in str(self.mobile or "") if ch.isdigit())
            query = {"$or": [{"auth.phone": phone_digits}, {"mobile": phone_digits}, {"phone": phone_digits}]}
            
            # Update in both govtlist and users collections
            govt_collection.update_one(query, {"$set": {
                "profilePhoto": photo_url,
                "profile.profilePhoto": photo_url,
                "profile.photo_url": photo_url,
                "profile.photo": photo_url
            }})
            users_collection.update_one(query, {"$set": {
                "profilePhoto": photo_url,
                "profile.profilePhoto": photo_url,
                "profile.photo_url": photo_url,
                "profile.photo": photo_url
            }})
            logger.info("[ClaimProcessing] Updated profile photo url for mobile: %s", self.mobile)
        except Exception as e:
            logger.warning(f"[ClaimProcessing] Failed to update profile photo: {e}")

    def process_claim(self):
        is_valid, error = self.validate_upload()
        if not is_valid:
            return {"ok": False, "message": error, "claim_id": self.claim_id}

        try:
            # 1. Upload files to storage (returns dict mapping field -> list of urls)
            uploaded_urls = self.upload_document()
            self._fetch_profile_documents(uploaded_urls)
            
            bill_urls = uploaded_urls.get("bills", [])
            if not bill_urls:
                bill_urls = [p for p in self.temp_paths if p]

            # 2. Extract new passport photo URL if uploaded
            passport_photo_url = uploaded_urls.get("passport_photo", [None])[0] if uploaded_urls.get("passport_photo") else None
            
            # 3. Generate and upload official application documents (PDF, DOCX, HTML)
            from services.government_application_generator import generate_and_upload_application
            
            claim_data = {k: v for k, v in self.form_data.items()}
            # Associate attachment public URLs in claim_data
            for key, urls in uploaded_urls.items():
                if key != "bills" and key != "passport_photo" and urls:
                    claim_data[key] = urls[0]
            
            # Fallback to existing photo if no new photo uploaded
            if not passport_photo_url:
                passport_photo_url = self.form_data.get("existing_passport_photo") or None
            if not passport_photo_url:
                try:
                    from database.mongo_client import govt_collection, users_collection
                    phone_digits = "".join(ch for ch in str(self.mobile or "") if ch.isdigit())
                    query = {"$or": [{"auth.phone": phone_digits}, {"mobile": phone_digits}, {"phone": phone_digits}]}
                    p_doc = govt_collection.find_one(query) or users_collection.find_one(query) or {}
                    passport_photo_url = (
                        p_doc.get("profilePhoto") or
                        p_doc.get("profile", {}).get("profilePhoto") or
                        p_doc.get("profile", {}).get("photo") or
                        p_doc.get("profile", {}).get("photo_url") or
                        (p_doc.get("documents", {}).get("profilePhoto", {}).get("url") if isinstance(p_doc.get("documents", {}).get("profilePhoto"), dict) else p_doc.get("documents", {}).get("profilePhoto"))
                    )
                except Exception as doc_err:
                    logger.warning(f"[ClaimProcessingPhoto] Failed to resolve profile photo from DB: {doc_err}")
            
            gen_docs = generate_and_upload_application(self.claim_id, claim_data, photo_url=passport_photo_url)

            # 4. If a new passport photo was uploaded, update the pensioner profile
            if passport_photo_url:
                self.update_user_profile_photo(passport_photo_url)
                # Auto-regenerate e-Health card on profile photo update
                try:
                    from database.mongo_client import govt_collection, users_collection
                    phone_digits = "".join(ch for ch in str(self.mobile or "") if ch.isdigit())
                    query = {"$or": [{"auth.phone": phone_digits}, {"mobile": phone_digits}, {"phone": phone_digits}]}
                    updated_profile = govt_collection.find_one(query) or users_collection.find_one(query)
                    if updated_profile:
                        from services.ecard_generator import generate_and_save_ecard
                        generate_and_save_ecard(self.mobile, updated_profile)
                except Exception as e:
                    logger.warning(f"[ClaimPhotoUpdate] Auto e-card regeneration failed: {e}")

            # 5. Build memory state and execute the AI agents pipeline
            workflow_memory = self.build_memory(bill_urls=bill_urls)
            
            # Link generated application files and extra attachments in memory form_data
            workflow_memory.form_data["generated_application"] = gen_docs
            workflow_memory.form_data["attachments"] = {k: v for k, v in uploaded_urls.items() if k != "bills"}
            
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
        # Clean all temporary paths recorded in dict
        for field_name, paths in getattr(self, "all_temp_paths", {}).items():
            for path in paths:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
        # Fallback cleanup for temp_paths list
        for path in self.temp_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


class ClaimProcessingError(Exception):
    """Exception raised during claim processing flow."""
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
