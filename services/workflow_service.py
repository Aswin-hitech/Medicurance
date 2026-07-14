from datetime import datetime, timezone

from database.mongo_client import claim_versions_collection, claims_collection, notifications_collection
from utils.logger import log_audit, log_claim_state

ALLOWED_STATUSES = {"Pending", "Approved", "Rejected", "Escalated"}


def _normalize_status(status):
    if status is None:
        return None
    status_str = str(status).strip().title()
    return status_str if status_str in ALLOWED_STATUSES else "Pending"


def record_claim_status_change(claim_id, old_status, new_status, actor, reason=None, update_fields=None):
    """
    Update a claim status and record the transition in claim logs,
    notifications, and audit logs.
    """
    normalized_new = _normalize_status(new_status)
    normalized_old = _normalize_status(old_status) if old_status else None
    now = datetime.now(timezone.utc).isoformat()

    update_doc = {
        "status": normalized_new,
        "updated_at": now,
    }
    if update_fields:
        update_doc.update(update_fields)

    claim_doc = claims_collection.find_one({"claim_id": claim_id}) or {}

    claims_collection.update_one(
        {"claim_id": claim_id},
        {"$set": update_doc}
    )
    updated_claim = claims_collection.find_one({"claim_id": claim_id}) or {}
    claim_versions_collection.insert_one({
        **updated_claim,
        "event": "status_changed",
        "actor": actor,
        "reason": reason,
        "created_at": now,
    })

    log_claim_state(
        claim_id=claim_id,
        old_state=normalized_old or "None",
        new_state=normalized_new,
        actor=actor,
        reason=reason or "Claim workflow update"
    )

    notifications_collection.insert_one({
        "claim_id": claim_id,
        "recipient": claim_doc.get("mobile", actor),
        "status": normalized_new,
        "message": reason or f"Claim status changed to {normalized_new}",
        "read": False,
        "created_at": now,
    })

    log_audit(
        actor=actor,
        action="claim_status_change",
        description=f"Claim {claim_id} updated to {normalized_new}",
        metadata={
            "old_status": normalized_old,
            "new_status": normalized_new,
            "reason": reason,
        },
    )

    return normalized_new


def reserve_claim_for_officer(claim_id, officer_name, officer_id=None, actor=None):
    """
    Mark a pending claim as being handled by a specific officer.
    Returns a dict with ok/status/message.
    """
    now = datetime.now(timezone.utc).isoformat()
    claim = claims_collection.find_one({"claim_id": claim_id}) or {}
    if not claim:
        return {"ok": False, "status": "missing", "message": "Claim not found."}

    current_owner = str(claim.get("handled_by") or "").strip()
    current_owner_id = str(claim.get("handled_by_id") or "").strip()
    officer_name = str(officer_name or "").strip() or "Claims Officer"
    officer_id = str(officer_id or "").strip() or officer_name

    if current_owner and current_owner_id and (current_owner_id != officer_id or current_owner != officer_name):
        return {
            "ok": False,
            "status": "locked",
            "message": f"This claim is already handled by {current_owner}.",
            "handled_by": current_owner,
            "handled_by_id": current_owner_id,
        }

    claims_collection.update_one(
        {
            "claim_id": claim_id,
            "$or": [
                {"handled_by": {"$exists": False}},
                {"handled_by": ""},
                {"handled_by_id": {"$exists": False}},
                {"handled_by_id": ""},
                {"handled_by_id": officer_id},
            ],
        },
        {"$set": {
            "handled_by": officer_name,
            "handled_by_id": officer_id,
            "handled_at": now,
            "handled_by_role": "officer",
            "updated_at": now,
        }},
    )

    updated = claims_collection.find_one({"claim_id": claim_id}) or {}
    if updated.get("handled_by") not in {officer_name} and updated.get("handled_by_id") not in {officer_id}:
        return {
            "ok": False,
            "status": "locked",
            "message": f"This claim is already handled by {updated.get('handled_by') or 'another officer'}.",
            "handled_by": updated.get("handled_by"),
            "handled_by_id": updated.get("handled_by_id"),
        }

    if actor:
        log_audit(actor, "claim_reserved", f"Claim {claim_id} handled by {officer_name}", {"handled_by": officer_name, "handled_by_id": officer_id})

    return {
        "ok": True,
        "status": "reserved",
        "message": f"Claim is now handled by {officer_name}.",
        "handled_by": officer_name,
        "handled_by_id": officer_id,
    }


def claim_is_locked_for_officer(claim, officer_name=None, officer_id=None):
    claim = claim or {}
    current_owner = str(claim.get("handled_by") or "").strip()
    current_owner_id = str(claim.get("handled_by_id") or "").strip()
    officer_name = str(officer_name or "").strip()
    officer_id = str(officer_id or "").strip()
    if not current_owner and not current_owner_id:
        return False
    if officer_name and current_owner == officer_name:
        return False
    if officer_id and current_owner_id == officer_id:
        return False
    return True


def update_status(claim_id, status):
    claim = claims_collection.find_one({"claim_id": claim_id}) or {}
    return record_claim_status_change(
        claim_id=claim_id,
        old_status=claim.get("status"),
        new_status=status,
        actor="system",
        reason="Status updated through workflow service"
    )


def get_claims_by_status(status):
    normalized = _normalize_status(status)
    return list(
        claims_collection.find({"status": normalized})
    )
