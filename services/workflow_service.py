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
