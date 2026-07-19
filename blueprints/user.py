from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, render_template, request, send_file, session, url_for, jsonify
from utils.auth_utils import role_required

from database.mongo_client import claims_collection, users_collection, govt_collection
from database.govt_repository import get_employee_by_aadhaar, get_employee_by_mobile, get_employee_by_ppo
from database.hospital_repository import get_all_hospitals
from database.claim_repository import get_claims_by_user
from utils.status_utils import normalize_claim_status
from services.claim_processing_service import ClaimProcessingService
from services.auth_service import resolve_role
from services.claim_view_service import enrich_claim_for_view
from utils.rate_limiter import limit_route
from utils.logger import log_audit
from services.pensioner_service import build_pensioner_profile, upload_owner_document, list_owner_documents, delete_owner_document, search_claims_knowledge
from config.settings import Config

user_bp = Blueprint('user', __name__)

@user_bp.route("/dashboard")
@role_required("user")
def dashboard():
    return render_template("user_dashboard.html")

@user_bp.route("/faq")
@role_required("user")
def faq():
    return render_template("faq.html")

@user_bp.route("/faq/download_annexure")
@role_required("user")
def download_annexure():
    import os
    path = os.path.join("resources", "annexures", "annexure_I.pdf")
    if not os.path.exists(path):
        flash("Annexure document is currently unavailable.", "warning")
        return redirect(url_for("user.faq"))
    return send_file(path, as_attachment=True, download_name="Annexure_I.pdf", mimetype="application/pdf")

@user_bp.route("/chat")
@role_required(["user", "officer", "admin"])
def chat():
    return render_template("chatbot.html")


@user_bp.route("/claim_request")
@role_required("user")
def claim_request():
    mobile = session.get("mobile") or session.get("user_id")
    resolved = resolve_role(mobile, preferred_role="user")
    user = resolved.get("document") or {}

    if not user and mobile:
        from database.user_repository import get_user_by_mobile
        user = get_user_by_mobile(mobile) or {}


    employee = None
    if user.get("ppo_number"):
        employee = get_employee_by_ppo(user.get("ppo_number"))
    if not employee and user.get("aadhaar_number"):
        employee = get_employee_by_aadhaar(user.get("aadhaar_number"))
    if not employee:
        employee = get_employee_by_mobile(mobile)

    if not user:
        user = {"mobile": mobile, "role": "user", "is_government_employee": False}

    safe_employee = employee or {}
    profile_data = build_pensioner_profile(safe_employee or user)
    return render_template("claim_request.html", profile_data=profile_data, user=user, mobile=mobile)

@user_bp.route("/submit_claim", methods=["POST"])
@role_required("user")
@limit_route("10 per hour", redirect_endpoint="user.claim_request", message="Too many claim submissions. Please try again later.")
def submit_claim():
    mobile = session.get("mobile") or session.get("user_id")

    phone_digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    govt_user = govt_collection.find_one({"$or": [{"auth.phone": phone_digits}, {"mobile": phone_digits}, {"phone": phone_digits}]})
    
    if govt_user:
        is_govt_verified = True
    else:
        user_doc = users_collection.find_one({"mobile": phone_digits}) or {}
        is_govt_verified = user_doc.get("is_government_employee") == True

    if not is_govt_verified:
        log_audit(mobile, "claim_blocked", "Unverified user attempted to submit a claim")
        flash("Please complete government identity verification before submitting claims.", "danger")
        return redirect(url_for('user.profile'))

    # Collect all uploaded files by key
    attachments = {}
    
    bills = request.files.getlist("bills")
    if not bills or len(bills) == 0 or (len(bills) == 1 and bills[0].filename == ''):
        if "bill" in request.files and request.files["bill"].filename != '':
            bills = [request.files["bill"]]
            
    attachments["bills"] = [f for f in bills if f and f.filename != '']
    
    for key in ["prescriptions", "discharge_summary", "investigation_reports", "certificates", "id_proof", "ppo_proof", "passport_photo"]:
        files_for_key = request.files.getlist(key)
        filtered = [f for f in files_for_key if f and f.filename != '']
        if filtered:
            attachments[key] = filtered

    if not attachments["bills"]:
        flash("No medical bills document uploaded", "danger")
        return redirect(url_for('user.claim_request'))

    service = ClaimProcessingService(mobile, request.form, attachments)

    try:
        result = service.process_claim()
        if not result.get("ok"):
            flash(result.get("message", "Unable to submit claim."), "danger")
            return redirect(url_for("user.claim_request"))

        decision = result.get("decision", "Pending")
        # Return success screen to citizen without technical agentic jargon
        claim_data = result.get("claim") or {}
        pdf_url = claim_data.get("generated_application", {}).get("pdf_url")
        return render_template(
            "claim_submit_success.html",
            claim_id=result.get("claim_id"),
            pdf_url=pdf_url,
            message="Your application has been sent successfully."
        )
    except Exception as e:
        flash(f"Error processing claim: {str(e)}", "danger")
        return redirect(url_for('user.claim_request'))


@user_bp.route("/edit_claim/<claim_id>", methods=["GET", "POST"])
@role_required("user")
def edit_claim(claim_id):
    mobile = session.get("mobile") or session.get("user_id")
    claim = claims_collection.find_one({"claim_id": claim_id, "mobile": mobile})
    
    if not claim:
        flash("Claim not found.", "danger")
        return redirect(url_for("user.claim_status"))
        
    if normalize_claim_status(claim.get("status")) != "Hold":
        flash("Only claims on Hold can be edited.", "warning")
        return redirect(url_for("user.claim_status"))
        
    if request.method == "GET":
        return render_template("edit_claim.html", claim=claim)
        
    # POST
    bills = request.files.getlist("bills")
    # If no new files provided, we might still want to update other details.
    # The simplest logic is to run the processing service again with whatever they uploaded, 
    # or fallback to their existing files if nothing new is provided.
    # To avoid complicating the OCR engine, we mandate uploading the corrected bill.
    if not bills or len(bills) == 0 or (len(bills) == 1 and bills[0].filename == ''):
        flash("Please upload the corrected documents to proceed.", "warning")
        return render_template("edit_claim.html", claim=claim)
        
    service = ClaimProcessingService(mobile, request.form, bills, claim_id=claim_id)
    try:
        result = service.process_claim()
        if not result.get("ok"):
            flash(result.get("message", "Unable to update claim."), "danger")
            return render_template("edit_claim.html", claim=claim)
        flash("Claim updated and resubmitted successfully for officer review.", "success")
    except Exception as e:
        flash(f"Error updating claim: {str(e)}", "danger")

    return redirect(url_for('user.claim_status'))

@user_bp.route("/claim_status")
@role_required("user")
def claim_status():
    mobile = session.get("mobile") or session.get("user_id")
    page = max(int(request.args.get("page", 1)), 1)
    limit = min(max(int(request.args.get("limit", 25)), 1), 100)
    total_claims = claims_collection.count_documents({"mobile": mobile})
    total_pages = max((total_claims + limit - 1) // limit, 1)
    page = min(page, total_pages)
    skip = (page - 1) * limit
    claims = get_claims_by_user(mobile, skip=skip, limit=limit)
    for claim in claims:
        claim.update(enrich_claim_for_view(claim))
        # Dynamic fallback to generate application form on the fly if missing
        if not claim.get("generated_application") or not claim.get("generated_application", {}).get("pdf_url"):
            try:
                from services.government_application_generator import generate_and_upload_application
                phone_digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
                pensioner = govt_collection.find_one({"$or": [{"mobile": phone_digits}, {"phone": phone_digits}]}) or users_collection.find_one({"$or": [{"mobile": phone_digits}, {"phone": phone_digits}]}) or {}
                
                passport_photo_url = (
                    pensioner.get("profilePhoto") or
                    pensioner.get("profile", {}).get("profilePhoto") or
                    pensioner.get("profile", {}).get("photo") or
                    pensioner.get("profile", {}).get("photo_url") or
                    (pensioner.get("documents", {}).get("profilePhoto", {}).get("url") if isinstance(pensioner.get("documents", {}).get("profilePhoto"), dict) else pensioner.get("documents", {}).get("profilePhoto"))
                )
                
                claim_id = claim.get("claim_id")
                claim_data = {k: v for k, v in claim.items()}
                gen_docs = generate_and_upload_application(claim_id, claim_data, photo_url=passport_photo_url)
                if gen_docs:
                    claims_collection.update_one({"claim_id": claim_id}, {"$set": {"generated_application": gen_docs}})
                    claim["generated_application"] = gen_docs
            except Exception as gen_err:
                print(f"[UserClaimStatusAppGen] Failed to generate application on the fly: {gen_err}")

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


    employee = None
    if user.get("ppo_number"):
        employee = get_employee_by_ppo(user.get("ppo_number"))
    if not employee and user.get("aadhaar_number"):
        employee = get_employee_by_aadhaar(user.get("aadhaar_number"))
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
    profile_data = build_pensioner_profile(safe_employee or user)
    documents = list_owner_documents(str(user.get("ppo_number") or mobile))
    return render_template("profile.html", mobile=mobile, employee=safe_employee, user=user, profile_data=profile_data, documents=documents, **stats)


@user_bp.route("/profile/update", methods=["POST"])
@role_required("user")
def update_profile():
    mobile = session.get("mobile") or session.get("user_id")
    if not mobile:
        flash("Profile not found", "warning")
        return redirect(url_for("auth.login"))

    updates = {
        "email": request.form.get("email"),
        "phone": request.form.get("phone"),
        "address": {
            "doorNo": request.form.get("doorNo"),
            "street": request.form.get("street"),
            "area": request.form.get("area"),
            "village": request.form.get("village"),
            "taluk": request.form.get("taluk"),
            "city": request.form.get("city"),
            "district": request.form.get("district"),
            "state": request.form.get("state"),
            "pincode": request.form.get("pincode"),
        },
        "medical": {
            "bloodGroup": request.form.get("bloodGroup"),
            "history": [item.strip() for item in request.form.get("medicalHistory", "").split(",") if item.strip()],
            "disabilities": request.form.get("disabilities"),
            "allergies": request.form.get("allergies"),
        },
        "emergency": {
            "name": request.form.get("emergencyName"),
            "relationship": request.form.get("emergencyRelationship"),
            "phone": request.form.get("emergencyPhone"),
        },
        "settings": {
            "language": request.form.get("language") or "English",
            "notifications": request.form.get("notifications") == "on",
            "theme": request.form.get("theme") or "light",
        },
    }
    update_doc = {}
    for key, value in updates.items():
        if isinstance(value, dict):
            # Only include nested dicts that have at least one non-None, non-empty value
            filtered = {k: v for k, v in value.items() if v not in (None, "")}
            if filtered:
                update_doc[key] = filtered
        elif value not in (None, "", {}, []):
            update_doc[key] = value
    update_doc["updatedAt"] = update_doc["updated_at"] = datetime.now(timezone.utc).isoformat()

    query = {"$or": [{"auth.phone": str(mobile)}, {"phone": str(mobile)}, {"mobile": str(mobile)}]}
    if request.form.get("email"):
        query["$or"].append({"auth.email": request.form.get("email").strip().lower()})
    result = govt_collection.update_one(query, {"$set": update_doc}, upsert=False)
    if result.matched_count == 0:
        # User may be in the legacy users collection instead of govtlist
        users_collection.update_one(query, {"$set": update_doc}, upsert=False)
        
    # Auto-regenerate e-Health card on profile changes
    try:
        updated_profile = get_employee_by_mobile(mobile) or users_collection.find_one(query)
        if updated_profile:
            from services.ecard_generator import generate_and_save_ecard
            generate_and_save_ecard(mobile, updated_profile)
    except Exception as e:
        logger.warning(f"[ProfileUpdate] Auto e-card regeneration failed: {e}")

    flash("Profile updated successfully.", "success")
    return redirect(url_for("user.profile"))


@user_bp.route("/documents", methods=["GET", "POST"])
@role_required("user")
def document_center():
    owner_id = str(session.get("user_id") or session.get("mobile"))
    if request.method == "POST":
        document_type = request.form.get("document_type")
        file_obj = request.files.get("file")
        if not document_type or not file_obj:
            flash("Please choose a document and file.", "danger")
            return redirect(url_for("user.document_center"))
        record = upload_owner_document(owner_id, document_type, file_obj, Config.SUPABASE_BILL_BUCKET)
        flash(f"{document_type} uploaded successfully.", "success")
        log_audit(owner_id, "document_upload", f"Uploaded {document_type}", record)
        return redirect(url_for("user.document_center"))

    documents = list_owner_documents(owner_id)
    return render_template("document_center.html", documents=documents)


@user_bp.route("/documents/delete/<document_type>", methods=["POST"])
@role_required("user")
def delete_document(document_type):
    owner_id = str(session.get("user_id") or session.get("mobile"))
    delete_owner_document(owner_id, document_type)
    flash("Document removed.", "success")
    return redirect(url_for("user.document_center"))


@user_bp.route("/know-your-claims")
@role_required("user")
def know_your_claims():
    query = request.args.get("q", "")
    results = search_claims_knowledge(query, k=10) if query else search_claims_knowledge("reimbursement treatment entitlement", k=10)
    filters = {
        "treatment": request.args.get("treatment", ""),
        "department": request.args.get("department", ""),
        "hospital_category": request.args.get("hospital_category", ""),
        "coverage_amount": request.args.get("coverage_amount", ""),
    }
    return render_template("know_your_claims.html", results=results, query=query, filters=filters)


@user_bp.route("/api/know-your-claims")
@role_required("user")
def know_your_claims_api():
    query = request.args.get("q", "")
    return jsonify({"results": search_claims_knowledge(query, k=10) if query else search_claims_knowledge("reimbursement treatment entitlement", k=10)})

@user_bp.route("/api/hospitals")
def get_hospitals_api():
    hospitals = get_all_hospitals()
    return jsonify([{"name": h["name"], "network": h.get("network", True)} for h in hospitals])

@user_bp.route("/download_letter/<claim_id>")
@role_required("user")
def download_letter(claim_id):
    claim = claims_collection.find_one({"claim_id": claim_id, "mobile": session.get("mobile") or session.get("user_id")})
    if not claim or normalize_claim_status(claim["status"]) == "Pending":
        flash("Letter not available.", "danger")
        return redirect(url_for('user.claim_status'))
        
    return redirect(url_for('user.generate_letter', claim_id=claim_id, letter_type='officer_to_beneficiary', download=1))


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
        claim = claims_collection.find_one({"claim_id": claim_id, "mobile": session.get("mobile") or session.get("user_id")})

    if not claim:
        flash("Letter not available.", "danger")
        if session.get("role") == "admin":
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("user.claim_status") if session.get("role") == "user" else url_for("officer.dashboard"))

    safe_type = str(letter_type).replace("/", "_")
    letter_info = claim.get("generated_letters", {}).get(safe_type, {})
    url_or_path = letter_info.get("url")

    try:
        if not url_or_path or not (url_or_path.startswith("http") or __import__('os').path.exists(url_or_path)):
            url_or_path = generate_pdf_letter(claim, letter_type)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("[User] Letter generation failed: %s", exc)
        flash("Letter generation failed. Please try again later.", "danger")
        if session.get("role") == "admin":
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("user.claim_status") if session.get("role") == "user" else url_for("officer.dashboard"))

    if url_or_path.startswith("http"):
        if request.args.get("download") == "1":
            url_or_path = url_or_path.replace("download=false", "download=true")
            if "download=" not in url_or_path:
                url_or_path += "&download=true" if "?" in url_or_path else "?download=true"
        return redirect(url_or_path)

    return send_file(
        url_or_path,
        as_attachment=request.args.get("download") == "1",
        download_name=f"Claim_{claim_id[-8:]}_{letter_type}.pdf",
        mimetype="application/pdf",
    )


@user_bp.route("/profile/regenerate_card", methods=["POST"])
@role_required("user")
def regenerate_card():
    mobile = session.get("mobile") or session.get("user_id")
    if not mobile:
        flash("Profile not found", "warning")
        return redirect(url_for("auth.login"))

    safe_employee = get_employee_by_mobile(mobile)
    user_doc = users_collection.find_one({"mobile": "".join(ch for ch in str(mobile) if ch.isdigit())}) or {}
    profile_doc = safe_employee or user_doc

    if not profile_doc:
        flash("Could not retrieve profile for card generation.", "danger")
        return redirect(url_for("user.profile"))

    from services.ecard_generator import generate_and_save_ecard
    assets = generate_and_save_ecard(mobile, profile_doc)
    if assets:
        flash("e-Health Card regenerated successfully.", "success")
    else:
        flash("Failed to regenerate e-Health Card. Please check your profile details.", "danger")

    return redirect(url_for("user.profile"))


@user_bp.route("/profile/download_card/<format_type>")
@role_required("user")
def download_card(format_type):
    mobile = session.get("mobile") or session.get("user_id")
    if not mobile:
        flash("Profile not found", "warning")
        return redirect(url_for("auth.login"))

    safe_employee = get_employee_by_mobile(mobile)
    user_doc = users_collection.find_one({"mobile": "".join(ch for ch in str(mobile) if ch.isdigit())}) or {}
    profile_doc = safe_employee or user_doc

    ecard = profile_doc.get("ecard", {})
    if not ecard:
        # Auto generate if missing
        from services.ecard_generator import generate_and_save_ecard
        ecard = generate_and_save_ecard(mobile, profile_doc) or {}

    if not ecard:
        flash("e-Health Card has not been generated yet. Please complete your profile details.", "warning")
        return redirect(url_for("user.profile"))

    if format_type == "pdf":
        url = ecard.get("pdf_url")
    elif format_type == "front":
        url = ecard.get("front_url")
    elif format_type == "back":
        url = ecard.get("back_url")
    else:
        flash("Invalid download format.", "danger")
        return redirect(url_for("user.profile"))

    if not url:
        flash("Requested file is not available.", "danger")
        return redirect(url_for("user.profile"))

    if "download=" not in url:
        url += "&download=true" if "?" in url else "?download=true"
    else:
        url = url.replace("download=false", "download=true")

    return redirect(url)
