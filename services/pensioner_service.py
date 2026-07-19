from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Iterable

from database.document_repository import DocumentRepository
from database.mongo_client import db
from services.llm_service import ask_llm
from services.rag_service import retrieve_rules
from services.storage_service import build_storage_path, delete_file, upload_file


DOCUMENT_FOLDERS = {
    "profilePhoto": "profile-images",
    "ppo": "ppo",
    "aadhaar": "aadhaar",
    "pan": "pan",
    "voterId": "voter-id",
    "bankPassbook": "bank-passbook",
    "digitalLifeCertificate": "digital-life-certificate",
    "pensionPhotoCard": "pension-photo-card",
    "annexures": "annexures",
    "rag": "rag-documents",
}


def _documents():
    return DocumentRepository(db)


def _now():
    return datetime.now(timezone.utc).isoformat()


def build_pensioner_profile(employee: Dict[str, Any]) -> Dict[str, Any]:
    employee = employee or {}
    auth = employee.get("auth", {})
    profile = employee.get("profile", {})
    address = employee.get("address", {})
    pension = employee.get("pension", {})
    identity = employee.get("identity", {})
    bank = employee.get("bank", {})
    medical = employee.get("medical", {})
    nominee = employee.get("nominee", {})
    emergency = employee.get("emergency", {})
    documents = employee.get("documents", {})
    activity = employee.get("activity", {})

    editable_fields = {"fullName", "phone", "email", "address", "emergency", "medical", "settings", "profilePhoto"}
    return {
        "auth": auth,
        "profile": profile,
        "address": address,
        "pension": pension,
        "identity": identity,
        "bank": bank,
        "medical": medical,
        "nominee": nominee,
        "emergency": emergency,
        "documents": documents,
        "activity": activity,
        "claimEligibility": employee.get("claimEligibility", True),
        "profileCompletion": employee.get("profileCompletion", 0),
        "settings": employee.get("settings", {}),
        "editable_fields": editable_fields,
        "last_updated": employee.get("updatedAt") or employee.get("updated_at"),
        "ecard": employee.get("ecard", {}),
    }


def upload_owner_document(owner_id: str, document_type: str, file_storage, bucket_name: str, claim_id: str | None = None):
    repo = _documents()
    existing = repo.get_document(owner_id, document_type)
    if existing and existing.get("storage_path"):
        delete_file(existing["storage_path"], bucket_name=bucket_name)

    suffix = Path(file_storage.filename or "").suffix or ""
    with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        file_storage.save(temp_file.name)
        folder = DOCUMENT_FOLDERS.get(document_type, document_type)
        storage_path = build_storage_path(file_storage.filename or "document", folder=folder)
        public_url = upload_file(
            temp_file.name,
            filename=file_storage.filename,
            bucket_name=bucket_name,
            folder=folder,
            storage_path=storage_path,
        )
    record = {
        "owner_id": owner_id,
        "document_type": document_type,
        "claim_id": claim_id,
        "url": public_url,
        "storage_path": storage_path,
        "upload_time": _now(),
        "verified": False,
        "file_type": file_storage.mimetype or "application/octet-stream",
        "updated_at": _now(),
    }
    repo.save_document(record)

    # Sync and update document link inside pensioner's MongoDB profile
    try:
        from database.mongo_client import govt_collection, users_collection
        phone_digits = "".join(ch for ch in str(owner_id) if ch.isdigit())
        query = {"$or": [
            {"ppo_number": owner_id},
            {"auth.ppoNumber": owner_id},
            {"auth.phone": phone_digits},
            {"mobile": phone_digits},
            {"phone": phone_digits}
        ]}
        
        update_doc = {
            f"documents.{document_type}": {
                "url": public_url,
                "verified": False,
                "uploadedAt": _now()
            },
            "updatedAt": _now()
        }
        
        if document_type == "profilePhoto":
            update_doc.update({
                "profilePhoto": public_url,
                "profile.profilePhoto": public_url,
                "profile.photo_url": public_url,
                "profile.photo": public_url
            })
            
        govt_collection.update_one(query, {"$set": update_doc})
        users_collection.update_one(query, {"$set": update_doc})
        
        # Auto-regenerate ecard if profile photo changes
        if document_type == "profilePhoto":
            try:
                updated_profile = govt_collection.find_one(query) or users_collection.find_one(query)
                if updated_profile:
                    from services.ecard_generator import generate_and_save_ecard
                    generate_and_save_ecard(owner_id, updated_profile)
            except Exception as ecard_err:
                logger.warning(f"[DocCenterPhoto] Auto e-card regeneration failed: {ecard_err}")
                
    except Exception as db_err:
        logger.warning(f"[UploadDoc] Failed to sync document URL to MongoDB: {db_err}")

    return record


def delete_owner_document(owner_id: str, document_type: str):
    repo = _documents()
    doc = repo.get_document(owner_id, document_type)
    if doc and doc.get("storage_path"):
        delete_file(doc["storage_path"])
    repo.delete_document(owner_id, document_type)

    # Remove reference from pensioner's MongoDB profile
    try:
        from database.mongo_client import govt_collection, users_collection
        phone_digits = "".join(ch for ch in str(owner_id) if ch.isdigit())
        query = {"$or": [
            {"ppo_number": owner_id},
            {"auth.ppoNumber": owner_id},
            {"auth.phone": phone_digits},
            {"mobile": phone_digits},
            {"phone": phone_digits}
        ]}
        
        unset_doc = {
            f"documents.{document_type}": ""
        }
        if document_type == "profilePhoto":
            unset_doc.update({
                "profilePhoto": "",
                "profile.profilePhoto": "",
                "profile.photo_url": "",
                "profile.photo": ""
            })
            
        govt_collection.update_one(query, {"$unset": unset_doc})
        users_collection.update_one(query, {"$unset": unset_doc})
    except Exception as db_err:
        logger.warning(f"[DeleteDoc] Failed to unsync document URL from MongoDB: {db_err}")

    return True


def list_owner_documents(owner_id: str):
    repo = _documents()
    docs = repo.get_documents(owner_id=owner_id)
    return sorted(docs, key=lambda item: item.get("upload_time") or item.get("created_at") or "", reverse=True)


def _simple_summary(rule_text: str, fallback: str) -> str:
    fallback = fallback or "This claim is available as per the annexure."
    if not rule_text:
        return fallback

    prompt = (
        "Rewrite the following pension claim rule in very simple language for an elderly pensioner. "
        "Use short sentences, avoid jargon, and keep the meaning accurate. "
        "Return only the rewritten text.\n\n"
        f"Rule: {rule_text}"
    )
    try:
        llm_summary = ask_llm(prompt)
        if isinstance(llm_summary, str):
            candidate = llm_summary.strip().strip('"')
            if candidate:
                return candidate
    except Exception:
        pass

    first_sentence = re.split(r"(?<=[.!?])\s+", rule_text.strip())[0] if rule_text.strip() else ""
    return first_sentence[:220] or fallback


def search_claims_knowledge(query: str, k: int = 5):
    results = retrieve_rules(query, k=k)
    formatted = []
    for item in results:
        rule_text = item.get("matched_rule", "")
        amount_match = re.search(r"(?:Rs\.?|â‚¹)\s?[\d,]+(?:\.\d+)?", rule_text, re.IGNORECASE)
        first_sentence = re.split(r"(?<=[.!?])\s+", rule_text.strip())[0] if rule_text.strip() else ""
        formatted.append({
            "claim_name": first_sentence[:140] or item.get("source_document", "Annexure rule"),
            "surgery": item.get("matched_rule", ""),
            "coverage": item.get("confidence"),
            "eligibility": item.get("matched_rule", ""),
            "maximum_reimbursement": amount_match.group(0) if amount_match else "See source document",
            "required_documents": [],
            "waiting_period": "See source document",
            "hospital_category": "All applicable",
            "annexure_reference": item.get("source_document"),
            "source_document": item.get("source_document"),
            "confidence": item.get("confidence", 0.0),
            "chunk_id": item.get("chunk_id"),
            "details": _simple_summary(rule_text, item.get("source_document", "Annexure rule")),
        })
    return formatted
