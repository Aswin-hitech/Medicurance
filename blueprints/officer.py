from datetime import datetime
from flask import Blueprint, render_template, redirect, session, flash, url_for, request
from utils.auth_utils import role_required
from database.mongo_client import claims_collection
from services.workflow_service import record_claim_status_change, reserve_claim_for_officer, claim_is_locked_for_officer
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
    officer_name = session.get("officer_name") or session.get("user_name") or session.get("mobile") or "Claims Officer"
    officer_id = session.get("user_id") or session.get("mobile")
    
    current_owner = claim.get("handled_by")
    current_owner_id = claim.get("handled_by_id")
    is_assigned_to_me = False
    
    if current_owner and current_owner_id:
        if current_owner_id != str(officer_id) and current_owner != officer_name:
            flash(f"This claim is already handled by {current_owner}.", "warning")
            return redirect(url_for("officer.dashboard"))
        is_assigned_to_me = True

    claim = enrich_claim_for_view(claim)
    
    # Retrieve pensioner's ecard info
    from database.mongo_client import govt_collection, users_collection
    ppo = claim.get("ppo_number")
    mobile = claim.get("mobile")
    phone_digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    pensioner = govt_collection.find_one({"$or": [{"ppo_number": ppo}, {"mobile": phone_digits}, {"phone": phone_digits}]})
    if not pensioner:
        pensioner = users_collection.find_one({"$or": [{"ppo_number": ppo}, {"mobile": phone_digits}, {"phone": phone_digits}]}) or {}
    ecard = pensioner.get("ecard", {})

    # Dynamic fallback to generate application form on the fly if missing
    if not claim.get("generated_application") or not claim.get("generated_application", {}).get("pdf_url"):
        try:
            from services.government_application_generator import generate_and_upload_application
            claim_data = {k: v for k, v in claim.items()}
            passport_photo_url = (
                pensioner.get("profilePhoto") or
                pensioner.get("profile", {}).get("profilePhoto") or
                pensioner.get("profile", {}).get("photo") or
                pensioner.get("profile", {}).get("photo_url") or
                (pensioner.get("documents", {}).get("profilePhoto", {}).get("url") if isinstance(pensioner.get("documents", {}).get("profilePhoto"), dict) else pensioner.get("documents", {}).get("profilePhoto"))
            )
            gen_docs = generate_and_upload_application(claim_id, claim_data, photo_url=passport_photo_url)
            if gen_docs:
                claims_collection.update_one({"claim_id": claim_id}, {"$set": {"generated_application": gen_docs}})
                claim["generated_application"] = gen_docs
        except Exception as gen_err:
            print(f"[ReviewPageAppGen] Failed to generate application on the fly: {gen_err}")

    return render_template("officer_claim_review.html", claim=claim, ecard=ecard, is_assigned_to_me=is_assigned_to_me)

@officer_bp.route("/assign/<claim_id>", methods=["POST"])
@role_required("officer")
def assign_claim(claim_id):
    officer_name = session.get("officer_name") or session.get("user_name") or session.get("mobile") or "Claims Officer"
    officer_id = session.get("user_id") or session.get("mobile")
    
    from services.workflow_service import reserve_claim_for_officer
    reserve = reserve_claim_for_officer(claim_id, officer_name, officer_id=officer_id, actor=session.get("mobile", "officer"))
    if reserve.get("ok"):
        flash("Claim assigned to you successfully.", "success")
    else:
        flash(reserve.get("message", "Could not assign claim to you."), "danger")
    return redirect(url_for("officer.review_page", claim_id=claim_id))

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
    officer_name = session.get("officer_name") or session.get("user_name") or session.get("mobile") or "Claims Officer"
    officer_id = session.get("user_id") or session.get("mobile")
    if claim_is_locked_for_officer(claim, officer_name, officer_id):
        flash(f"This claim is already handled by {claim.get('handled_by')}.", "warning")
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
            "handled_by": officer_name,
            "handled_by_id": officer_id,
            "handled_at": datetime.now().isoformat(),
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
    officer_name = session.get("officer_name") or session.get("user_name") or session.get("mobile") or "Claims Officer"
    officer_id = session.get("user_id") or session.get("mobile")
    if claim_is_locked_for_officer(claim, officer_name, officer_id):
        flash(f"This claim is already handled by {claim.get('handled_by')}.", "warning")
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
            "handled_by": officer_name,
            "handled_by_id": officer_id,
            "handled_at": datetime.now().isoformat(),
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

@officer_bp.route("/hold_claim/<claim_id>", methods=["POST"])
@role_required("officer")
def hold_claim(claim_id):
    reason = request.form.get("reason")
    actor = session.get("mobile", "officer")
    claim = claims_collection.find_one({"claim_id": claim_id})
    if not claim or normalize_claim_status(claim.get("status")) != "Pending":
        log_audit(actor, "security_violation", f"Invalid hold attempt for claim {claim_id}")
        flash("Claim is not available for hold.", "danger")
        return redirect(url_for('officer.dashboard'))
    officer_name = session.get("officer_name") or session.get("user_name") or session.get("mobile") or "Claims Officer"
    officer_id = session.get("user_id") or session.get("mobile")
    if claim_is_locked_for_officer(claim, officer_name, officer_id):
        flash(f"This claim is already handled by {claim.get('handled_by')}.", "warning")
        return redirect(url_for('officer.dashboard'))
    
    record_claim_status_change(
        claim_id=claim_id,
        old_status="Pending",
        new_status="Hold",
        actor=actor,
        reason=f"Placed on hold: {reason}",
        update_fields={
            "hold_reason": reason,
            "officer_notes": reason,
            "handled_by": officer_name,
            "handled_by_id": officer_id,
            "handled_at": datetime.now().isoformat(),
        }
    )
    
    log_audit(actor, "hold_claim", f"Placed claim {claim_id} on hold", {"reason": reason})
    flash(f"Claim {claim_id[-8:]} placed on hold pending beneficiary action.", "warning")
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
    officer_name = session.get("officer_name") or session.get("user_name") or session.get("mobile") or "Claims Officer"
    officer_id = session.get("user_id") or session.get("mobile")
    if claim_is_locked_for_officer(claim, officer_name, officer_id):
        flash(f"This claim is already handled by {claim.get('handled_by')}.", "warning")
        return redirect(url_for('officer.dashboard'))
    record_claim_status_change(
        claim_id=claim_id,
        old_status="Pending",
        new_status="Escalated",
        actor=actor,
        reason=f"Escalated: {reason}",
        update_fields={
            "escalation_reason": reason,
            "handled_by": officer_name,
            "handled_by_id": officer_id,
            "handled_at": datetime.now().isoformat(),
        }
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


@officer_bp.route("/verify_card/<token>")
@role_required(["officer", "admin"])
def verify_card(token):
    from services.ecard_generator import decode_verification_token
    from services.pensioner_service import build_pensioner_profile
    from database.mongo_client import claims_collection, govt_collection, users_collection
    from services.auth_service import resolve_role
    
    payload, error = decode_verification_token(token)
    if error:
        flash(f"Verification Failed: {error}", "danger")
        return redirect(url_for("officer.dashboard"))
        
    ppo_number = payload.get("sub")
    mobile = payload.get("mobile")
    
    phone_digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    
    # Query pensioner
    safe_employee = govt_collection.find_one({"$or": [{"ppo_number": ppo_number}, {"mobile": phone_digits}, {"phone": phone_digits}]})
    user_doc = users_collection.find_one({"$or": [{"ppo_number": ppo_number}, {"mobile": phone_digits}, {"phone": phone_digits}]}) or {}
    profile_doc = safe_employee or user_doc
    
    if not profile_doc:
        flash("Beneficiary profile not found for this card.", "danger")
        return redirect(url_for("officer.dashboard"))
        
    # Build pensioner profile
    profile_data = build_pensioner_profile(profile_doc)
    
    # Fetch active claim history
    claims = list(claims_collection.find({"mobile": phone_digits}).sort("created_at", -1))
    for c in claims:
        c["_id"] = str(c["_id"])
    
    # Fetch previous AI trust scores
    trust_scores = []
    for c in claims:
        t_score = c.get("trust_result", {}).get("score") or c.get("trust_score")
        if t_score is not None:
            trust_scores.append({
                "claim_id": c["claim_id"],
                "date": c.get("created_at") or c.get("date"),
                "score": round(float(t_score), 1),
                "status": c.get("status", "Pending")
            })
            
    # Hospital eligibility rules lookup
    hospital_eligibility = "Eligible for Cashless Reimbursement" if profile_data.get("claimEligibility", True) else "Claim eligibility suspended"
    
    return render_template(
        "verify_card.html",
        profile_data=profile_data,
        claims=claims,
        trust_scores=trust_scores,
        hospital_eligibility=hospital_eligibility,
        issued_at=profile_doc.get("ecard", {}).get("issued_at"),
        ecard=profile_doc.get("ecard", {})
    )
