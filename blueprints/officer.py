from flask import Blueprint, render_template, redirect, session, flash, url_for, request
from utils.auth_utils import role_required
from database.mongo_client import claims_collection
from services.workflow_service import record_claim_status_change
from services.letter_generator import generate_pdf_letter
from services.claim_view_service import enrich_claim_for_view
from utils.status_utils import normalize_claim_status
from utils.logger import log_audit

officer_bp = Blueprint('officer', __name__, url_prefix='/officer')

@officer_bp.route("/")
@role_required("officer")
def dashboard():
    page = max(int(request.args.get("page", 1)), 1)
    limit = min(max(int(request.args.get("limit", 25)), 1), 100)

    claims_cursor = claims_collection.find().sort("created_at", -1)
    total_claims = claims_collection.count_documents({})
    total_pages = max((total_claims + limit - 1) // limit, 1)
    page = min(page, total_pages)
    skip = (page - 1) * limit
    claims = list(claims_cursor.skip(skip).limit(limit))
    for claim in claims:
        claim["status"] = normalize_claim_status(claim.get("status"))

    status_counts = {"Pending": 0, "Approved": 0, "Rejected": 0, "Escalated": 0}
    for row in claims_collection.aggregate([
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]):
        status = normalize_claim_status(row.get("_id"))
        if status in status_counts:
            status_counts[status] = int(row.get("count", 0) or 0)

    pending_count = status_counts["Pending"]
    approved_count = status_counts["Approved"]
    rejected_count = status_counts["Rejected"]
    escalated_count = status_counts["Escalated"]

    stats = {
        "approved_claims": approved_count,
        "rejected_claims": rejected_count,
        "escalated_claims": escalated_count
    }

    return render_template(
        "officer_dashboard.html",
        claims=claims,
        pending_count=pending_count,
        stats=stats,
        page=page,
        total_pages=total_pages,
        limit=limit,
        total_claims=total_claims
    )

@officer_bp.route("/review/<claim_id>")
@role_required("officer")
def review_page(claim_id):
    claim = claims_collection.find_one({"claim_id": claim_id})
    if not claim:
        flash("Claim not found", "danger")
        return redirect(url_for('officer.dashboard'))
    claim = enrich_claim_for_view(claim)
    return render_template("officer_claim_review.html", claim=claim)

@officer_bp.route("/approve/<claim_id>", methods=["POST"])
@role_required("officer")
def approve(claim_id):
    notes = request.form.get("notes")
    sanctioned_amount = request.form.get("sanctioned_amount")
    actor = session.get("mobile", "officer")
    claim = claims_collection.find_one({"claim_id": claim_id})
    if not claim or normalize_claim_status(claim.get("status")) != "Pending":
        log_audit(actor, "security_violation", f"Invalid approve attempt for claim {claim_id}")
        flash("Claim is not available for approval.", "danger")
        return redirect(url_for('officer.dashboard'))
    record_claim_status_change(
        claim_id=claim_id,
        old_status="Pending",
        new_status="Approved",
        actor=actor,
        reason=f"Approved with notes: {notes}",
        update_fields={
            "officer_notes": notes,
            "approval_reason": notes,
            "sanctioned_amount": float(sanctioned_amount or claim.get("amount") or 0),
        }
    )
    updated_claim = claims_collection.find_one({"claim_id": claim_id}) or claim
    try:
        generate_pdf_letter(updated_claim, "approval")
    except Exception as exc:
        log_audit(actor, "letter_generation_failed", f"Approval letter generation failed for {claim_id}: {exc}")
        flash("Claim approved, but letter generation needs regeneration from the claim page.", "warning")
    log_audit(actor, "approve_claim", f"Approved claim {claim_id}")
    flash(f"Claim {claim_id[-8:]} approved successfully.", "success")
    return redirect(url_for('officer.dashboard'))

@officer_bp.route("/reject/<claim_id>", methods=["POST"])
@role_required("officer")
def reject(claim_id):
    reason = request.form.get("reason")
    comments = request.form.get("comments")
    actor = session.get("mobile", "officer")
    claim = claims_collection.find_one({"claim_id": claim_id})
    if not claim or normalize_claim_status(claim.get("status")) != "Pending":
        log_audit(actor, "security_violation", f"Invalid reject attempt for claim {claim_id}")
        flash("Claim is not available for rejection.", "danger")
        return redirect(url_for('officer.dashboard'))
    record_claim_status_change(
        claim_id=claim_id,
        old_status="Pending",
        new_status="Rejected",
        actor=actor,
        reason=f"Rejected reason: {reason}",
        update_fields={
            "rejection_reason": reason,
            "officer_comments": comments,
            "officer_notes": comments,
        }
    )
    updated_claim = claims_collection.find_one({"claim_id": claim_id}) or claim
    try:
        generate_pdf_letter(updated_claim, "rejection")
    except Exception as exc:
        log_audit(actor, "letter_generation_failed", f"Rejection letter generation failed for {claim_id}: {exc}")
        flash("Claim rejected, but letter generation needs regeneration from the claim page.", "warning")
    log_audit(actor, "reject_claim", f"Rejected claim {claim_id}", {"reason": reason})
    flash(f"Claim {claim_id[-8:]} rejected.", "warning")
    return redirect(url_for('officer.dashboard'))

@officer_bp.route("/escalate/<claim_id>", methods=["POST"])
@role_required("officer")
def escalate(claim_id):
    reason = request.form.get("reason", "Needs manual verification")
    actor = session.get("mobile", "officer")
    claim = claims_collection.find_one({"claim_id": claim_id})
    if not claim or normalize_claim_status(claim.get("status")) != "Pending":
        log_audit(actor, "security_violation", f"Invalid escalate attempt for claim {claim_id}")
        flash("Claim is not available for escalation.", "danger")
        return redirect(url_for('officer.dashboard'))
    record_claim_status_change(
        claim_id=claim_id,
        old_status="Pending",
        new_status="Escalated",
        actor=actor,
        reason=f"Escalated: {reason}",
        update_fields={"escalation_reason": reason}
    )
    log_audit(actor, "escalate_claim", f"Escalated claim {claim_id}", {"reason": reason})
    flash(f"Claim {claim_id[-8:]} escalated for further review.", "info")
    return redirect(url_for('officer.dashboard'))

@officer_bp.route("/profile")
@role_required("officer")
def profile():
    from database.mongo_client import claim_logs_collection
    from services.auth_service import resolve_role
    
    mobile = session.get("mobile") or session.get("user_id")
    resolved = resolve_role(mobile, preferred_role="officer")
    officer = resolved.get("document") or {}
    
    # Calculate stats
    actor_id = session.get("mobile", "officer")
    approved_count = claim_logs_collection.count_documents({"actor": actor_id, "new_state": "Approved"})
    rejected_count = claim_logs_collection.count_documents({"actor": actor_id, "new_state": "Rejected"})
    escalated_count = claim_logs_collection.count_documents({"actor": actor_id, "new_state": "Escalated"})
    reviewed_count = claim_logs_collection.count_documents({"actor": actor_id})
    
    stats = {
        "reviewed": reviewed_count,
        "approved": approved_count,
        "rejected": rejected_count,
        "escalated": escalated_count
    }
    
    return render_template("officer_profile.html", officer=officer, stats=stats)
