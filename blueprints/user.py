from flask import Blueprint, flash, redirect, render_template, request, send_file, session, url_for
from utils.auth_utils import role_required

from database.mongo_client import claims_collection, users_collection
from database.hospital_repository import get_all_hospitals
from database.claim_repository import get_claims_by_user
from utils.status_utils import normalize_claim_status
from services.claim_processing_service import ClaimProcessingService
from services.auth_service import resolve_role
from services.claim_view_service import enrich_claim_for_view
from utils.rate_limiter import limit_route
from utils.logger import log_audit

user_bp = Blueprint('user', __name__)

@user_bp.route("/dashboard")
@role_required("user")
def dashboard():
    return render_template("user_dashboard.html")

@user_bp.route("/claim_request")
@role_required("user")
def claim_request():
    return render_template("claim_request.html")

@user_bp.route("/submit_claim", methods=["POST"])
@role_required("user")
@limit_route("10 per hour", redirect_endpoint="user.claim_request", message="Too many claim submissions. Please try again later.")
def submit_claim():
    mobile = session["mobile"]

    user_doc = users_collection.find_one({"mobile": mobile}) or {}
    if not user_doc.get("is_government_employee"):
        log_audit(mobile, "claim_blocked", "Unverified user attempted to submit a claim")
        flash("Please complete government identity verification before submitting claims.", "danger")
        return redirect(url_for('user.profile'))

    if "bill" not in request.files:
        flash("No document uploaded", "danger")
        return redirect(url_for('user.claim_request'))

    file = request.files["bill"]
    service = ClaimProcessingService(mobile, request.form, file)

    try:
        result = service.process_claim()
        if not result.get("ok"):
            flash(result.get("message", "Unable to submit claim."), "danger")
            return redirect(url_for("user.claim_request"))

        decision = result.get("decision", "Pending")
        if decision == "Approve":
            flash("Claim approved by the agentic workflow.", "success")
        elif decision == "Reject":
            flash("Claim rejected by the agentic workflow.", "warning")
        elif decision == "Request Additional Documents":
            flash("Additional documents are required before the claim can move forward.", "warning")
        else:
            flash("Claim submitted successfully for officer review.", "success")
    except Exception as e:
        flash(f"Error processing claim: {str(e)}", "danger")

    return redirect(url_for('user.claim_status'))

@user_bp.route("/claim_status")
@role_required("user")
def claim_status():
    mobile = session["mobile"]
    page = max(int(request.args.get("page", 1)), 1)
    limit = min(max(int(request.args.get("limit", 25)), 1), 100)
    total_claims = claims_collection.count_documents({"mobile": mobile})
    total_pages = max((total_claims + limit - 1) // limit, 1)
    page = min(page, total_pages)
    skip = (page - 1) * limit
    claims = get_claims_by_user(mobile, skip=skip, limit=limit)
    for claim in claims:
        claim.update(enrich_claim_for_view(claim))
    return render_template("claim_status.html", claims=claims, page=page, total_pages=total_pages, limit=limit)

@user_bp.route("/profile")
@role_required("user")
def profile():
    mobile = session.get("mobile") or session.get("user_id")
    if not mobile:
        flash("Profile not found", "warning")
        return redirect(url_for("auth.login"))

    claims = get_claims_by_user(mobile)
    for claim in claims:
        claim["status"] = normalize_claim_status(claim.get("status"))
    resolved = resolve_role(mobile, preferred_role="user")
    user = resolved.get("document") or {}

    if not user and mobile:
        from database.user_repository import get_user_by_mobile
        user = get_user_by_mobile(mobile) or {}

    from database.govt_repository import get_employee_by_employee_id, get_employee_by_mobile

    employee = None
    if user.get("employee_id"):
        employee = get_employee_by_employee_id(user.get("employee_id"))
    if not employee:
        employee = get_employee_by_mobile(mobile)

    if not user:
        user = {"mobile": mobile, "role": "user", "is_government_employee": False}

    stats = {
        "pending_count": len([c for c in claims if normalize_claim_status(c['status']) == 'Pending']),
        "approved_count": len([c for c in claims if normalize_claim_status(c['status']) == 'Approved']),
        "rejected_count": len([c for c in claims if normalize_claim_status(c['status']) == 'Rejected'])
    }
    safe_employee = employee or {}
    return render_template("profile.html", mobile=mobile, employee=safe_employee, user=user, **stats)

@user_bp.route("/api/hospitals")
def get_hospitals_api():
    hospitals = get_all_hospitals()
    # Return minimal data for the dropdown
    return [{"name": h["name"], "network": h.get("network", True)} for h in hospitals]

@user_bp.route("/download_letter/<claim_id>")
@role_required("user")
def download_letter(claim_id):
    from services.letter_generator import generate_pdf_letter

    claim = claims_collection.find_one({"claim_id": claim_id, "mobile": session["mobile"]})
    if not claim or normalize_claim_status(claim["status"]) == "Pending":
        flash("Letter not available.", "danger")
        return redirect(url_for('user.claim_status'))
        
    action_type = "approval" if normalize_claim_status(claim["status"]) == "Approved" else "rejection"
    
    safe_type = str(action_type).replace("/", "_")
    letter_info = claim.get("generated_letters", {}).get(safe_type, {})
    if letter_info.get("url"):
        return redirect(letter_info.get("url"))

    pdf_path_or_url = generate_pdf_letter(claim, action_type)
    if pdf_path_or_url.startswith("http"):
        return redirect(pdf_path_or_url)
    
    return send_file(pdf_path_or_url, as_attachment=True, download_name=f"Claim_{claim_id[-8:]}_{action_type}.pdf")


@user_bp.route("/generate_letter/<claim_id>/<letter_type>")
@role_required(["user", "officer", "admin"])
def generate_letter(claim_id, letter_type):
    from services.letter_generator import generate_pdf_letter

    supported_letter_types = {
        "beneficiary_to_officer",
        "officer_to_beneficiary",
        "officer_to_hospital",
    }
    legacy_letter_types = {"approval", "rejection"}
    allowed_types = supported_letter_types | legacy_letter_types

    if letter_type not in allowed_types:
        flash("Unsupported letter type.", "danger")
        if session.get("role") == "admin":
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("user.claim_status") if session.get("role") == "user" else url_for("officer.dashboard"))

    if session.get("role") in {"officer", "admin"}:
        claim = claims_collection.find_one({"claim_id": claim_id})
    else:
        claim = claims_collection.find_one({"claim_id": claim_id, "mobile": session["mobile"]})

    if not claim:
        flash("Letter not available.", "danger")
        if session.get("role") == "admin":
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("user.claim_status") if session.get("role") == "user" else url_for("officer.dashboard"))

    safe_type = str(letter_type).replace("/", "_")
    letter_info = claim.get("generated_letters", {}).get(safe_type, {})
    if letter_info.get("url"):
        return redirect(letter_info.get("url"))

    pdf_path_or_url = generate_pdf_letter(claim, letter_type)
    if pdf_path_or_url.startswith("http"):
        return redirect(pdf_path_or_url)

    return send_file(
        pdf_path_or_url,
        as_attachment=request.args.get("download") == "1",
        download_name=f"Claim_{claim_id[-8:]}_{letter_type}.pdf",
        mimetype="application/pdf",
    )
