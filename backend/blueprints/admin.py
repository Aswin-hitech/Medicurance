import csv
import io
import math
import re
from pathlib import Path

from flask import Blueprint, current_app, flash, make_response, redirect, render_template, request, session, url_for, jsonify
from utils.auth_utils import role_required
from config.settings import Config
from database.mongo_client import claims_collection, db, govt_collection, hospitals_collection, officers_collection, users_collection
from database.hospital_repository import add_hospital, get_all_hospitals, remove_hospital, update_hospital_network, upsert_hospital, get_hospital_by_identifier
from utils.status_utils import normalize_claim_status
from services.claim_view_service import enrich_claim_for_view
from database.government_officer_repository import create_officer_account
from utils.logger import log_audit
from utils.masking import mask_document, mask_value
from utils.password_policy import validate_password_policy

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def _build_claim_query():
    clauses = []
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    hospital = request.args.get("hospital", "").strip()
    verification = request.args.get("verification", "").strip()

    if search:
        regex = {"$regex": re.escape(search), "$options": "i"}
        clauses.append({
            "$or": [
            {"claim_id": regex},
            {"name": regex},
            {"mobile": regex},
            {"hospital": regex},
            {"department": regex},
            {"status": regex},
            ]
        })

    if status:
        clauses.append({"status": normalize_claim_status(status)})

    if hospital:
        clauses.append({"hospital": {"$regex": re.escape(hospital), "$options": "i"}})

    if verification:
        if verification == "verified":
            clauses.append({
                "$or": [
                {"trust_result.level": {"$in": ["HIGH", "MEDIUM"]}},
                {"trust_result.score": {"$gte": 60}},
                ]
            })
        elif verification == "review":
            clauses.append({
                "$or": [
                {"trust_result.level": "LOW"},
                {"status": "Escalated"},
                ]
            })

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _claim_metrics(claim):
    trust_result = claim.get("trust_result") or {}
    ai_result = claim.get("ai_result") or {}
    fraud_result = claim.get("fraud_result") or {}
    ocr_conf = claim.get("ocr_confidence", claim.get("ocr_confidence_score", 0))
    image_hash = claim.get("image_hash")
    duplicate_count = claims_collection.count_documents({"image_hash": image_hash}) if image_hash else 0
    duplicate_probability = (claim.get("duplicate_result") or {}).get("duplicate_probability")
    if duplicate_probability is None:
        duplicate_probability = 0 if duplicate_count <= 1 else min(95, 60 + (duplicate_count - 1) * 10)
    reasoning = ai_result.get("reasoning") or trust_result.get("reasoning") or "No AI summary recorded."
    if isinstance(reasoning, list):
        summary = "; ".join(str(item) for item in reasoning if item)[:220]
    else:
        summary = str(reasoning).splitlines()[0][:220]

    return {
        "ocr_confidence": round(float(ocr_conf or 0), 1),
        "trust_score": round(float(trust_result.get("score", 0) or 0), 1),
        "trust_level": trust_result.get("level", "LOW"),
        "fraud_level": str(fraud_result.get("fraud_level") or ai_result.get("system_risk_level") or ai_result.get("risk_level") or "LOW").upper(),
        "verification_state": "Verified" if normalize_claim_status(claim.get("status")) in {"Approved", "Rejected"} else "Pending Review",
        "duplicate_probability": duplicate_probability,
        "hospital_verification": "In-Network" if claim.get("hospital_verified") or (fraud_result.get("hospital_verified") if isinstance(fraud_result, dict) else False) else ("Verified" if (ai_result.get("hospital_verified") if isinstance(ai_result, dict) else False) else "Manual"),
        "ai_reasoning_summary": summary,
    }


def _serialize_claim(claim):
    normalized = enrich_claim_for_view(claim)
    normalized["status"] = normalize_claim_status(claim.get("status"))
    normalized.update(_claim_metrics(claim))
    normalized["metrics"].update({
        "trust_score": normalized.get("trust_score", normalized["metrics"].get("trust_score")),
        "fraud_level": normalized.get("fraud_level", normalized["metrics"].get("fraud_level")),
        "ocr_confidence": normalized.get("ocr_confidence", normalized["metrics"].get("ocr_confidence")),
        "duplicate_probability": normalized.get("duplicate_probability", normalized["metrics"].get("duplicate_probability")),
        "hospital_verification": normalized.get("hospital_verification", normalized["metrics"].get("hospital_verification")),
        "reference_number": normalized.get("letter_reference") or normalized["metrics"].get("reference_number"),
    })
    return normalized


def _normalize_chart_rows(rows, limit=None):
    labels = []
    values = []
    for row in rows[:limit] if limit else rows:
        label = row.get("_id")
        if label in (None, ""):
            label = "Unknown"
        labels.append(str(label))
        values.append(int(row.get("count", 0) or 0))
    return {"labels": labels, "values": values}


def _aggregate_counts(pipeline, default_key="Unknown", limit=None):
    rows = list(claims_collection.aggregate(pipeline))
    normalized_rows = []
    for row in rows:
        label = row.get("_id")
        if label in (None, ""):
            label = default_key
        normalized_rows.append({"_id": label, "count": row.get("count", 0)})
    if limit:
        normalized_rows = normalized_rows[:limit]
    return _normalize_chart_rows(normalized_rows)


def _claim_dashboard_analytics():
    try:
        status_counts = {"Pending": 0, "Approved": 0, "Rejected": 0, "Escalated": 0}
        for row in claims_collection.aggregate([
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]):
            status = normalize_claim_status(row.get("_id"))
            if status in status_counts:
                status_counts[status] = int(row.get("count", 0) or 0)

        risk_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
        for row in claims_collection.aggregate([
            {
                "$project": {
                    "risk_level": {
                        "$toUpper": {
                            "$ifNull": [
                                "$fraud_result.fraud_level",
                                {"$ifNull": ["$ai_result.system_risk_level", "LOW"]},
                            ]
                        }
                    }
                }
            },
            {"$group": {"_id": "$risk_level", "count": {"$sum": 1}}},
        ]):
            key = str(row.get("_id") or "LOW").upper()
            risk_counts[key if key in risk_counts else "LOW"] = int(row.get("count", 0) or 0)

        duplicate_claims = 0
        duplicate_groups = 0
        for row in claims_collection.aggregate([
            {"$match": {"image_hash": {"$exists": True, "$ne": ""}}},
            {"$group": {"_id": "$image_hash", "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
            {"$group": {"_id": None, "duplicate_claims": {"$sum": "$count"}, "duplicate_groups": {"$sum": 1}}},
        ]):
            duplicate_claims = int(row.get("duplicate_claims", 0) or 0)
            duplicate_groups = int(row.get("duplicate_groups", 0) or 0)

        monthly_data = _aggregate_counts([
            {
                "$project": {
                    "month": {
                        "$substrCP": [
                            {
                                "$ifNull": [
                                    {"$toString": {"$ifNull": ["$created_at", "$date"]}},
                                    "",
                                ]
                            },
                            0,
                            7,
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": {
                        "$cond": [
                            {"$or": [{"$eq": ["$month", ""]}, {"$eq": ["$month", None]}]},
                            "Unknown",
                            "$month",
                        ]
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ], default_key="Unknown")

        hospital_data = _aggregate_counts([
            {"$group": {"_id": {"$ifNull": ["$hospital", "Unknown"]}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ], default_key="Unknown", limit=12)

        dept_data = _aggregate_counts([
            {"$group": {"_id": {"$ifNull": ["$department", "Unlinked"]}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ], default_key="Unlinked", limit=12)

        fraud_data = _normalize_chart_rows([
            {"_id": "LOW", "count": risk_counts["LOW"]},
            {"_id": "MEDIUM", "count": risk_counts["MEDIUM"]},
            {"_id": "HIGH", "count": risk_counts["HIGH"]},
        ])

        return {
            "status_counts": status_counts,
            "risk_counts": risk_counts,
            "duplicate_claims": duplicate_claims,
            "duplicate_groups": duplicate_groups,
            "monthly_data": monthly_data,
            "hospital_data": hospital_data,
            "dept_data": dept_data,
            "fraud_data": fraud_data,
        }
    except Exception:
        status_counts = {"Pending": 0, "Approved": 0, "Rejected": 0, "Escalated": 0}
        risk_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
        monthly_map = {}
        hospital_map = {}
        dept_map = {}
        image_hash_counts = {}

        for claim in claims_collection.find():
            status = normalize_claim_status(claim.get("status"))
            if status in status_counts:
                status_counts[status] += 1

            risk = str((claim.get("fraud_result") or {}).get("fraud_level") or (claim.get("ai_result") or {}).get("system_risk_level") or "LOW").upper()
            risk_counts[risk if risk in risk_counts else "LOW"] += 1

            created = str(claim.get("created_at") or claim.get("date") or "")
            month = created[:7] if len(created) >= 7 else "Unknown"
            monthly_map[month or "Unknown"] = monthly_map.get(month or "Unknown", 0) + 1

            hospital_key = claim.get("hospital") or "Unknown"
            hospital_map[hospital_key] = hospital_map.get(hospital_key, 0) + 1

            dept_key = claim.get("department") or "Unlinked"
            dept_map[dept_key] = dept_map.get(dept_key, 0) + 1

            image_hash = claim.get("image_hash")
            if image_hash:
                image_hash_counts[image_hash] = image_hash_counts.get(image_hash, 0) + 1

        duplicate_claims = sum(count for count in image_hash_counts.values() if count > 1)
        duplicate_groups = sum(1 for count in image_hash_counts.values() if count > 1)

        monthly_sorted = sorted(monthly_map.keys())
        monthly_data = {"labels": monthly_sorted, "values": [monthly_map[key] for key in monthly_sorted]}
        hospital_sorted = sorted(hospital_map.items(), key=lambda item: item[1], reverse=True)[:12]
        dept_sorted = sorted(dept_map.items(), key=lambda item: item[1], reverse=True)[:12]

        return {
            "status_counts": status_counts,
            "risk_counts": risk_counts,
            "duplicate_claims": duplicate_claims,
            "duplicate_groups": duplicate_groups,
            "monthly_data": monthly_data,
            "hospital_data": {"labels": [item[0] for item in hospital_sorted], "values": [item[1] for item in hospital_sorted]},
            "dept_data": {"labels": [item[0] for item in dept_sorted], "values": [item[1] for item in dept_sorted]},
            "fraud_data": {
                "labels": ["LOW", "MEDIUM", "HIGH"],
                "values": [risk_counts["LOW"], risk_counts["MEDIUM"], risk_counts["HIGH"]],
            },
        }

@admin_bp.route("/")
@role_required("admin")
def dashboard():
    users_total = users_collection.count_documents({})
    hospitals = get_all_hospitals()
    query = _build_claim_query()

    sort_by = request.args.get("sort", "created_at").strip() or "created_at"
    order = request.args.get("order", "desc").strip().lower()
    if sort_by not in {"created_at", "amount", "status", "hospital"}:
        sort_by = "created_at"
    if order not in {"asc", "desc"}:
        order = "desc"
    sort_direction = 1 if order == "asc" else -1
    page = max(int(request.args.get("page", 1)), 1)
    limit = min(max(int(request.args.get("limit", 20)), 1), 100)

    total_claims = claims_collection.count_documents(query)
    total_pages = max((total_claims + limit - 1) // limit, 1)
    page = min(page, total_pages)
    skip = (page - 1) * limit

    claims_cursor = claims_collection.find(query).sort(sort_by, sort_direction).skip(skip).limit(limit)
    claims = [_serialize_claim(claim) for claim in claims_cursor]
    analytics = _claim_dashboard_analytics()

    stats = {
        "total_users": users_total,
        "total_claims": claims_collection.count_documents({}),
        "total_hospitals": len(hospitals),
        "pending_claims": analytics["status_counts"]["Pending"],
        "approved_claims": analytics["status_counts"]["Approved"],
        "rejected_claims": analytics["status_counts"]["Rejected"],
        "escalated_claims": analytics["status_counts"]["Escalated"],
        "high_risk_claims": analytics["risk_counts"]["HIGH"],
        "duplicate_claims": analytics["duplicate_claims"],
        "duplicate_groups": analytics["duplicate_groups"],
        "fraud_levels": analytics["risk_counts"],
        "govt_employees": govt_collection.count_documents({}),
        "officers": officers_collection.count_documents({}),
    }

    search = request.args.get("q", "").strip()
    selected_status = request.args.get("status", "").strip()
    selected_hospital = request.args.get("hospital", "").strip()
    selected_verification = request.args.get("verification", "").strip()

    return render_template(
        "admin_dashboard.html",
        hospitals=hospitals,
        stats=stats,
        claims=claims,
        monthly_data=analytics["monthly_data"],
        hospital_data=analytics["hospital_data"],
        dept_data=analytics["dept_data"],
        fraud_data=analytics["fraud_data"],
        page=page,
        total_pages=total_pages,
        limit=limit,
        search=search,
        selected_status=selected_status,
        selected_hospital=selected_hospital,
        selected_verification=selected_verification,
        sort_by=sort_by,
        sort_order=order,
        total_claims=stats["total_claims"],
    )


@admin_bp.route("/health")
@role_required("admin")
def system_health():
    checks = []

    def add_check(name, status, details, category):
        checks.append({
            "name": name,
            "status": status,
            "details": details,
            "category": category,
        })

    try:
        db.command("ping")
        add_check("MongoDB", "Healthy", "Database ping succeeded.", "Database")
    except Exception as exc:
        add_check("MongoDB", "Degraded", f"Database ping failed: {exc}", "Database")

    add_check(
        "Users Collection",
        "Healthy" if users_collection.estimated_document_count() >= 0 else "Degraded",
        f"{users_collection.count_documents({})} records available.",
        "Database",
    )
    add_check(
        "Claims Collection",
        "Healthy" if claims_collection.estimated_document_count() >= 0 else "Degraded",
        f"{claims_collection.count_documents({})} records available.",
        "Database",
    )
    add_check(
        "Hospitals Collection",
        "Healthy" if hospitals_collection.estimated_document_count() >= 0 else "Degraded",
        f"{hospitals_collection.count_documents({})} records available.",
        "Database",
    )
    add_check(
        "Officers Collection",
        "Healthy" if officers_collection.estimated_document_count() >= 0 else "Degraded",
        f"{officers_collection.count_documents({})} records available.",
        "Database",
    )
    add_check(
        "Govt Employee Collection",
        "Healthy" if govt_collection.estimated_document_count() >= 0 else "Degraded",
        f"{govt_collection.count_documents({})} records available.",
        "Database",
    )

    groq_ready = bool(Config.GROQ_API_KEY and Config.GROQ_MODEL)
    add_check(
        "Groq / LLM",
        "Configured" if groq_ready else "Missing",
        f"Model: {Config.GROQ_MODEL or 'not set'}",
        "AI",
    )

    ocr_ready = bool(Config.OCR_SPACE_API_KEY)
    poppler_exists = Path(Config.POPPLER_PATH).exists()
    add_check(
        "OCR",
        "Configured" if ocr_ready else "Missing",
        "OCR key is present; Poppler path " + ("exists." if poppler_exists else "is missing."),
        "AI",
    )

    vector_path = Path(Config.VECTOR_DB_PATH)
    annexure_paths = [
        Path(Config.ANNEXURE_PATH),
        Path(getattr(Config, "ANNEXURE_IA_PATH", "resources/annexures/annexure_IA.pdf")),
    ]
    rag_ready = (
        (vector_path / "index.faiss").exists()
        and (vector_path / "chunks.pkl").exists()
        and all(path.exists() for path in annexure_paths)
    )
    add_check(
        "RAG Assets",
        "Healthy" if rag_ready else "Missing",
        f"Vector store: {Config.VECTOR_DB_PATH}; Annexures: {', '.join(str(path) for path in annexure_paths)}",
        "AI",
    )

    supabase_ready = bool(Config.SUPABASE_URL and Config.SUPABASE_API_KEY and Config.SUPABASE_SECRET_KEY)
    add_check(
        "Supabase",
        "Configured" if supabase_ready else "Missing",
        "Storage credentials are " + ("present." if supabase_ready else "incomplete."),
        "Storage",
    )

    template_path = Path(__file__).resolve().parent.parent / "resources" / "letter_templates" / "medical_claim_template.docx"
    verified_stamp = Path(__file__).resolve().parent.parent / "resources" / "stamps" / "verified.png"
    declined_stamp = Path(__file__).resolve().parent.parent / "resources" / "stamps" / "declined.png"
    add_check(
        "Letter Template",
        "Healthy" if template_path.exists() else "Missing",
        str(template_path),
        "Documents",
    )
    add_check(
        "Rubber Stamps",
        "Healthy" if verified_stamp.exists() and declined_stamp.exists() else "Missing",
        "Verified and declined stamp assets are " + ("available." if verified_stamp.exists() and declined_stamp.exists() else "not fully available."),
        "Documents",
    )

    add_check(
        "Upload Limit",
        "Healthy" if current_app.config.get("MAX_CONTENT_LENGTH") else "Missing",
        f"Max upload size: {current_app.config.get('MAX_CONTENT_LENGTH', 'not set')} bytes.",
        "Security",
    )

    summary = {
        "healthy": len([item for item in checks if item["status"] in {"Healthy", "Configured"}]),
        "missing": len([item for item in checks if item["status"] == "Missing"]),
        "degraded": len([item for item in checks if item["status"] == "Degraded"]),
    }

    return render_template("admin_system_health.html", checks=checks, summary=summary)

@admin_bp.route("/create_officer")
@role_required("admin")
def create_officer_page():
    return render_template("admin_create_officer.html")

@admin_bp.route("/create_officer", methods=["POST"])
@role_required("admin")
def create_officer_submit():
    import bcrypt
    from database.government_officer_repository import get_officer_by_identifier
    
    officer_id = request.form.get("officer_id", "").strip()
    aadhaar_number = request.form.get("aadhaar_number", "").strip()
    phone_number = request.form.get("phone_number", "").strip() or request.form.get("mobile", "").strip() or request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password")
    joining_date = request.form.get("joining_date", "").strip() or request.form.get("date_of_joining", "").strip()

    identifier = officer_id or aadhaar_number or phone_number or email
    if not identifier:
        flash("Officer ID, Aadhaar number, phone number, or email is required.", "danger")
        return redirect(url_for('admin.create_officer_page'))
    
    if get_officer_by_identifier(identifier):
        flash("Officer already exists", "danger")
        return redirect(url_for('admin.create_officer_page'))

    ok, errors = validate_password_policy(password)
    if not ok:
        flash(" ".join(errors), "danger")
        return redirect(url_for('admin.create_officer_page'))
        
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    officer_payload = {
        "officer_id": officer_id or aadhaar_number or phone_number or identifier,
        "phone_number": phone_number,
        "phone": phone_number,
        "mobile": phone_number,
        "email": email,
        "aadhaar_number": aadhaar_number,
        "aadhaar_last4": aadhaar_number[-4:] if aadhaar_number else "",
        "joining_date": joining_date,
        "date_of_joining": joining_date,
        "password": hashed,
        "status": "Active",
        "role": "officer",
        "is_verified": False,
        "government_verified": False,
        "verification_completed": False,
    }
    create_officer_account(officer_payload)
    
    log_audit(session.get("user_id", session.get("mobile", "admin")), "create_officer", f"Created officer account for officer_id: {officer_payload['officer_id']}")
    
    flash(f"Officer account created for {officer_payload['officer_id']}", "success")
    return redirect(url_for('admin.dashboard'))


@admin_bp.route("/claims/<claim_id>")
@role_required("admin")
def claim_detail(claim_id):
    claim = claims_collection.find_one({"claim_id": claim_id})
    if not claim:
        flash("Claim not found.", "danger")
        return redirect(url_for("admin.dashboard"))

    return render_template("admin_claim_detail.html", claim=_serialize_claim(claim))


@admin_bp.route("/claims/export.csv")
@role_required("admin")
def export_claims_csv():
    query = _build_claim_query()
    rows = claims_collection.find(query).sort("created_at", -1)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "claim_id",
        "name",
        "mobile",
        "hospital",
        "amount",
        "status",
        "trust_score",
        "fraud_level",
        "ocr_confidence",
        "duplicate_probability",
        "verification_state",
        "created_at",
    ])
    for claim in rows:
        metrics = _claim_metrics(claim)
        writer.writerow([
            claim.get("claim_id"),
            claim.get("name"),
            claim.get("mobile"),
            claim.get("hospital"),
            claim.get("amount"),
            normalize_claim_status(claim.get("status")),
            metrics["trust_score"],
            metrics["fraud_level"],
            metrics["ocr_confidence"],
            metrics["duplicate_probability"],
            metrics["verification_state"],
            claim.get("created_at"),
        ])

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=medicurance_claims.csv"
    response.headers["Content-type"] = "text/csv"
    return response

@admin_bp.route("/add_hospital", methods=["POST"])
@role_required("admin")
def add_hospital_submit():
    hospital_data = {
        "hospitalId": request.form.get("hospitalId") or request.form.get("hospital_id"),
        "nhisCode": request.form.get("nhisCode"),
        "name": request.form.get("name"),
        "district": request.form.get("district"),
        "cluster": request.form.get("cluster"),
        "address": request.form.get("address"),
        "pincode": request.form.get("pincode"),
        "phone": [item.strip() for item in request.form.get("phone", "").split(",") if item.strip()],
        "email": request.form.get("email"),
        "location": {
            "type": "Point",
            "coordinates": [
                float(request.form.get("longitude") or 0),
                float(request.form.get("latitude") or 0),
            ],
        },
        "specialties": [item.strip() for item in request.form.get("specialties", "").split(",") if item.strip()],
        "facilities": [item.strip() for item in request.form.get("facilities", "").split(",") if item.strip()],
        "schemes": [item.strip() for item in request.form.get("schemes", "TN NHIS 2026").split(",") if item.strip()] or ["TN NHIS 2026"],
        "cashless": request.form.get("cashless") == "on",
        "timings": {
            "opd": request.form.get("opd"),
            "emergency": request.form.get("emergency") == "on",
        },
        "status": request.form.get("status") or "active",
    }
    
    try:
        existing = get_hospital_by_identifier(hospital_data["hospitalId"]) or get_hospital_by_identifier(hospital_data["nhisCode"])
        if existing:
            flash(f"Hospital '{hospital_data['name']}' already exists.", "warning")
            return redirect(url_for('admin.dashboard'))
        upsert_hospital(hospital_data)
        log_audit(session.get("user_id", "admin"), "add_hospital", f"Added hospital {hospital_data['name']}", {"hospitalId": hospital_data["hospitalId"], "nhisCode": hospital_data["nhisCode"]})
        flash(f"Hospital '{hospital_data['name']}' added successfully.", "success")
    except Exception as e:
        flash(f"Error adding hospital: {str(e)}", "danger")
    return redirect(url_for('admin.dashboard'))

@admin_bp.route("/delete_hospital/<identifier>", methods=["POST"])
@role_required("admin")
def delete_hospital_route(identifier):
    remove_hospital(identifier)
    log_audit(session.get("user_id", "admin"), "delete_hospital", f"Removed hospital {identifier}")
    flash(f"Hospital '{identifier}' removed.", "warning")
    return redirect(url_for('admin.dashboard'))

@admin_bp.route("/toggle_hospital_network/<identifier>", methods=["POST"])
@role_required("admin")
def toggle_hospital_network(identifier):
    hospital = get_hospital_by_identifier(identifier)
    if hospital:
        new_status = not hospital.get("cashless", hospital.get("network", False))
        update_hospital_network(identifier, new_status)
        log_audit(session.get("user_id", "admin"), "toggle_hospital_network", f"Changed hospital network status for {identifier}", {"cashless": new_status})
        flash(f"Status for '{identifier}' updated.", "success")
    return redirect(url_for('admin.dashboard'))

# ==========================================
# PHASE 3 - GOVT EMPLOYEE DATABASE MODULE
# ==========================================

@admin_bp.route("/govt_database")
@role_required("admin")
def govt_database():
    from database.govt_repository import get_basic_employee_data
    employees = get_basic_employee_data()
    return render_template("admin_govt_database.html", employees=employees)

@admin_bp.route("/govt_database/basic")
@role_required("admin")
def govt_basic_data():
    from database.govt_repository import get_basic_employee_data
    data = get_basic_employee_data()
    return jsonify(data)


@admin_bp.route("/govt_database/upload", methods=["POST"])
@role_required("admin")
def govt_database_upload():
    import os
    from werkzeug.utils import secure_filename
    from services.csv_import_service import validate_and_preview_csv
    
    if 'csv_file' not in request.files:
        flash("No file part selected.", "danger")
        return redirect(url_for('admin.govt_database'))
        
    file = request.files['csv_file']
    if file.filename == '':
        flash("No selected file.", "danger")
        return redirect(url_for('admin.govt_database'))
        
    if not file.filename.endswith('.csv'):
        flash("Only CSV files are allowed.", "danger")
        return redirect(url_for('admin.govt_database'))
        
    # Secure and save locally inside workspace
    filename = secure_filename(file.filename)
    # Ensure temporary upload directory exists
    temp_dir = os.path.join(os.getcwd(), "temp_imports")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, filename)
    file.save(temp_path)
    
    # Store path in session
    session["temp_import_path"] = temp_path
    
    # Validate and preview
    preview_results = validate_and_preview_csv(temp_path)
    
    if not preview_results.get("success"):
        flash(preview_results.get("message"), "danger")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return redirect(url_for('admin.govt_database'))
        
    return render_template(
        "admin_import_preview.html", 
        preview_rows=preview_results.get("preview_rows"),
        errors=preview_results.get("errors"),
        stats=preview_results.get("stats"),
        filename=filename
    )

@admin_bp.route("/govt_database/import", methods=["POST"])
@role_required("admin")
def govt_database_import_commit():
    import os
    from services.csv_import_service import validate_and_preview_csv
    from database.govt_repository import bulk_insert_employees
    
    temp_path = session.get("temp_import_path")
    if not temp_path or not os.path.exists(temp_path):
        flash("No uploaded data found to import.", "danger")
        return redirect(url_for('admin.govt_database'))
        
    results = validate_and_preview_csv(temp_path)
    clean_records = results.get("clean_records", [])
    
    success_count = 0
    failure_count = results.get("stats", {}).get("invalid", 0)
    
    if clean_records:
        try:
            bulk_insert_employees(clean_records)
            success_count = len(clean_records)
            flash(f"Successfully imported {success_count} government employee records!", "success")
        except Exception as e:
            flash(f"Bulk insert error: {str(e)}", "danger")
            
    # Cleanup file and session
    if os.path.exists(temp_path):
        os.remove(temp_path)
    session.pop("temp_import_path", None)
    
    return render_template(
        "admin_import_result.html",
        success_count=success_count,
        failure_count=failure_count
    )

@admin_bp.route("/govt_database/error_report")
@role_required("admin")
def govt_database_error_report():
    import os
    import csv
    import io
    from flask import make_response
    from services.csv_import_service import validate_and_preview_csv
    
    temp_path = session.get("temp_import_path")
    if not temp_path or not os.path.exists(temp_path):
        flash("No active CSV import session found to generate error report.", "danger")
        return redirect(url_for('admin.govt_database'))
        
    results = validate_and_preview_csv(temp_path)
    errors = results.get("errors", [])
    
    # Generate CSV in memory
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["Row Number", "PPO Number", "Validation Scope", "Error Description"])
    
    for err in errors:
        cw.writerow([err.get("row"), err.get("ppo_number") or err.get("employee_id"), err.get("field"), err.get("message")])
        
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=govt_employee_import_errors.csv"
    output.headers["Content-type"] = "text/csv"
    return output


# ==========================================
# PHASE 4 - SEPARATE GOVT OFFICER MODULE
# ==========================================

@admin_bp.route("/manage_officers")
@role_required("admin")
def manage_officers():
    from database.government_officer_repository import search_officer, get_all_officers
    
    query = request.args.get('q', '').strip()
    dept = request.args.get('department', '').strip()
    dist = request.args.get('district', '').strip()
    
    officers = search_officer(query=query, department=dept or None, district=dist or None)
    
    # Find unique departments and districts to populate the filters
    all_officers = get_all_officers()
    departments = sorted(list(set([o.get('department') for o in all_officers if o.get('department')])))
    districts = sorted(list(set([o.get('district') for o in all_officers if o.get('district')])))
    
    return render_template(
        "admin_manage_officers.html",
        officers=officers,
        departments=departments,
        districts=districts,
        selected_dept=dept,
        selected_dist=dist,
        search_query=query
    )

@admin_bp.route("/manage_officers/add", methods=["POST"])
@role_required("admin")
def manage_officers_add():
    from database.government_officer_repository import add_officer
    
    officer_data = {
        "officer_id": request.form.get("officer_id", "").strip(),
        "name": request.form.get("name", "").strip(),
        "gender": request.form.get("gender", "").strip(),
        "age": request.form.get("age", "").strip(),
        "date_of_birth": request.form.get("date_of_birth", "").strip(),
        "department": request.form.get("department", "").strip(),
        "designation": request.form.get("designation", "").strip(),
        "employee_type": request.form.get("employee_type", "").strip(),
        "experience_years": request.form.get("experience_years", "").strip(),
        "date_of_joining": request.form.get("date_of_joining", "").strip() or request.form.get("joining_date", "").strip(),
        "joining_date": request.form.get("joining_date", "").strip() or request.form.get("date_of_joining", "").strip(),
        "salary": request.form.get("salary", "").strip(),
        "blood_group": request.form.get("blood_group", "").strip(),
        "marital_status": request.form.get("marital_status", "").strip(),
        "address": request.form.get("address", "").strip(),
        "city": request.form.get("city", "").strip(),
        "district": request.form.get("district", "").strip(),
        "state": request.form.get("state", "").strip(),
        "pincode": request.form.get("pincode", "").strip(),
        "phone_number": request.form.get("phone_number", "").strip() or request.form.get("phone", "").strip(),
        "phone": request.form.get("phone_number", "").strip() or request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "aadhaar_number": request.form.get("aadhaar_number", "").strip(),
        "aadhaar_last4": request.form.get("aadhaar_last4", "").strip(),
        "pan_last4": request.form.get("pan_last4", "").strip(),
        "nominee_name": request.form.get("nominee_name", "").strip(),
        "relationship": request.form.get("relationship", "").strip(),
        "insurance_provider": request.form.get("insurance_provider", "").strip(),
        "policy_number": request.form.get("policy_number", "").strip(),
        "policy_start": request.form.get("policy_start", "").strip(),
        "policy_end": request.form.get("policy_end", "").strip(),
        "claim_eligibility": request.form.get("claim_eligibility", "").strip(),
        "medical_history": request.form.get("medical_history", "").strip(),
        "emergency_contact": request.form.get("emergency_contact", "").strip(),
        "emergency_phone": request.form.get("emergency_phone", "").strip(),
        "bank_name": request.form.get("bank_name", "").strip(),
        "account_last4": request.form.get("account_last4", "").strip(),
        "ifsc_code": request.form.get("ifsc_code", "").strip(),
        "status": "Active"
    }
    
    try:
        add_officer(officer_data)
        log_audit(session.get("user_id", "admin"), "create_officer", f"Added officer profile {officer_data.get('officer_id')}")
        flash(f"Officer '{officer_data['name']}' added successfully.", "success")
    except Exception as e:
        flash(f"Error adding officer: {str(e)}", "danger")
        
    return redirect(url_for('admin.manage_officers'))

@admin_bp.route("/manage_officers/edit/<officer_id>", methods=["POST"])
@role_required("admin")
def manage_officers_edit(officer_id):
    from database.government_officer_repository import update_officer
    
    update_data = {
        "name": request.form.get("name", "").strip(),
        "gender": request.form.get("gender", "").strip(),
        "age": request.form.get("age", "").strip(),
        "date_of_birth": request.form.get("date_of_birth", "").strip(),
        "department": request.form.get("department", "").strip(),
        "designation": request.form.get("designation", "").strip(),
        "employee_type": request.form.get("employee_type", "").strip(),
        "experience_years": request.form.get("experience_years", "").strip(),
        "date_of_joining": request.form.get("date_of_joining", "").strip() or request.form.get("joining_date", "").strip(),
        "joining_date": request.form.get("joining_date", "").strip() or request.form.get("date_of_joining", "").strip(),
        "salary": request.form.get("salary", "").strip(),
        "blood_group": request.form.get("blood_group", "").strip(),
        "marital_status": request.form.get("marital_status", "").strip(),
        "address": request.form.get("address", "").strip(),
        "city": request.form.get("city", "").strip(),
        "district": request.form.get("district", "").strip(),
        "state": request.form.get("state", "").strip(),
        "pincode": request.form.get("pincode", "").strip(),
        "phone_number": request.form.get("phone_number", "").strip() or request.form.get("phone", "").strip(),
        "phone": request.form.get("phone_number", "").strip() or request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "aadhaar_number": request.form.get("aadhaar_number", "").strip(),
        "aadhaar_last4": request.form.get("aadhaar_last4", "").strip(),
        "pan_last4": request.form.get("pan_last4", "").strip(),
        "nominee_name": request.form.get("nominee_name", "").strip(),
        "relationship": request.form.get("relationship", "").strip(),
        "insurance_provider": request.form.get("insurance_provider", "").strip(),
        "policy_number": request.form.get("policy_number", "").strip(),
        "policy_start": request.form.get("policy_start", "").strip(),
        "policy_end": request.form.get("policy_end", "").strip(),
        "claim_eligibility": request.form.get("claim_eligibility", "").strip(),
        "medical_history": request.form.get("medical_history", "").strip(),
        "emergency_contact": request.form.get("emergency_contact", "").strip(),
        "emergency_phone": request.form.get("emergency_phone", "").strip(),
        "bank_name": request.form.get("bank_name", "").strip(),
        "account_last4": request.form.get("account_last4", "").strip(),
        "ifsc_code": request.form.get("ifsc_code", "").strip()
    }
    
    try:
        update_officer(officer_id, update_data)
        log_audit(session.get("user_id", "admin"), "update_officer", f"Updated officer {officer_id}")
        flash(f"Officer details updated successfully.", "success")
    except Exception as e:
        flash(f"Error updating officer: {str(e)}", "danger")
        
    return redirect(url_for('admin.manage_officers'))

@admin_bp.route("/manage_officers/toggle/<officer_id>", methods=["POST"])
@role_required("admin")
def manage_officers_toggle(officer_id):
    from database.mongo_client import officers_collection
    from database.government_officer_repository import update_officer
    
    officer = officers_collection.find_one({"officer_id": officer_id})
    if officer:
        new_status = "Deactivated" if officer.get("status") == "Active" else "Active"
        update_officer(officer_id, {"status": new_status})
        log_audit(session.get("user_id", "admin"), "role_change", f"Officer {officer_id} status changed to {new_status}")
        flash(f"Officer status updated to '{new_status}'.", "success")
    else:
        flash("Officer not found.", "danger")
        
    return redirect(url_for('admin.manage_officers'))

@admin_bp.route("/manage_officers/delete/<officer_id>", methods=["POST"])
@role_required("admin")
def manage_officers_delete(officer_id):
    from database.government_officer_repository import delete_officer
    
    try:
        delete_officer(officer_id)
        log_audit(session.get("user_id", "admin"), "delete_officer", f"Deleted officer {officer_id}")
        flash(f"Officer deleted successfully.", "warning")
    except Exception as e:
        flash(f"Error deleting officer: {str(e)}", "danger")
        
    return redirect(url_for('admin.manage_officers'))


# ==========================================
# PHASE 6 - ADVANCED ADMIN INTELLIGENCE PANEL
# ==========================================

@admin_bp.route("/intelligence")
@role_required("admin")
def intelligence_dashboard():
    from database.mongo_client import (
        claims_collection, users_collection, hospitals_collection, 
        govt_collection, officers_collection
    )
    
    claims = list(claims_collection.find())
    users = list(users_collection.find())
    hospitals = list(hospitals_collection.find())
    govt_employees = list(govt_collection.find())
    officers = list(officers_collection.find())
    
    # Simple aggregations in python to populate cards
    stats = {
        "total_claims": len(claims),
        "pending_claims": len([c for c in claims if normalize_claim_status(c.get('status')) == 'Pending']),
        "approved_claims": len([c for c in claims if normalize_claim_status(c.get('status')) == 'Approved']),
        "rejected_claims": len([c for c in claims if normalize_claim_status(c.get('status')) == 'Rejected']),
        "escalated_claims": len([c for c in claims if normalize_claim_status(c.get('status')) == 'Escalated']),
        "fraud_flagged": len([c for c in claims if c.get('ai_result', {}).get('fraud_flags')]),
        "high_risk": len([c for c in claims if str(c.get('ai_result', {}).get('system_risk_level', '')).upper() == 'HIGH']),
        "govt_employees_count": len(govt_employees),
        "verified_employees_count": len(govt_employees) + len([u for u in users if u.get('is_government_employee')]),
        "hospitals_count": len(hospitals),
        "officers_count": len(officers)
    }
    
    # 1. Monthly Trends
    monthly_trends = {}
    for c in claims:
        dt = c.get('created_at', c.get('date', ''))
        if dt:
            month = dt[:7] # YYYY-MM
            monthly_trends[month] = monthly_trends.get(month, 0) + 1
            
    # Sort monthly trends chronologically
    sorted_months = sorted(list(monthly_trends.keys()))
    monthly_data = {
        "labels": sorted_months,
        "values": [monthly_trends[m] for m in sorted_months]
    }
    
    # 2. Department-wise Claims
    dept_claims = {}
    mobile_to_dept = {u.get('mobile'): u.get('department', 'Unlinked') for u in users if u.get('mobile')}
    for c in claims:
        mob = c.get('mobile')
        dept = mobile_to_dept.get(mob, 'Unlinked')
        dept_claims[dept] = dept_claims.get(dept, 0) + 1
        
    dept_data = {
        "labels": list(dept_claims.keys()),
        "values": list(dept_claims.values())
    }
    
    # 3. Hospital Usage
    hospital_usage = {}
    for c in claims:
        hosp = c.get('hospital', 'Unknown')
        hospital_usage[hosp] = hospital_usage.get(hosp, 0) + 1
        
    hospital_data = {
        "labels": list(hospital_usage.keys()),
        "values": list(hospital_usage.values())
    }
    
    # 4. Officer Activity
    officer_activity = {}
    for c in claims:
        reviewer = c.get('reviewed_by', c.get('officer_id', 'Unassigned'))
        officer_activity[reviewer] = officer_activity.get(reviewer, 0) + 1
        
    officer_data = {
        "labels": list(officer_activity.keys()),
        "values": list(officer_activity.values())
    }
    
    # 5. Fraud Distribution
    fraud_levels = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for c in claims:
        lvl = str(c.get('ai_result', {}).get('system_risk_level', 'LOW')).upper()
        if lvl in fraud_levels:
            fraud_levels[lvl] += 1
        else:
            fraud_levels["LOW"] += 1
            
    fraud_data = {
        "labels": list(fraud_levels.keys()),
        "values": list(fraud_levels.values())
    }
    
    # 6. Approval Ratio
    approval_ratio = {
        "labels": ["Approved", "Rejected", "Pending", "Escalated"],
        "values": [stats["approved_claims"], stats["rejected_claims"], stats["pending_claims"], stats["escalated_claims"]]
    }
    
    return render_template(
        "admin_intelligence.html",
        stats=stats,
        monthly_data=monthly_data,
        dept_data=dept_data,
        hospital_data=hospital_data,
        officer_data=officer_data,
        fraud_data=fraud_data,
        approval_ratio=approval_ratio
    )


# ==========================================
# PHASE 7 - CLAIM TRUST AI SYSTEM
# ==========================================

@admin_bp.route("/trust_analysis")
@role_required("admin")
def trust_analysis_dashboard():
    from database.mongo_client import claims_collection, users_collection, hospitals_collection, govt_collection
    
    claims = list(claims_collection.find().sort("created_at", -1))
    users = list(users_collection.find())
    hospitals = list(hospitals_collection.find())
    govt_employees = list(govt_collection.find())
    
    mobile_to_user = {u.get('mobile'): u for u in users if u.get('mobile')}
    hospital_dict = {str(h.get('name', '')).strip().lower(): h for h in hospitals if h.get('name')}
    
    analyzed_claims = []
    
    for c in claims:
        ai = c.get("ai_result", {})
        trust_result = c.get("trust_result") or {}
        mob = c.get("mobile", "")
        
        # 1. AI Confidence
        ai_confidence = 85.0
        if isinstance(ai, dict):
            # Try multiple keys representing confidence
            ai_confidence = float(ai.get("confidence_score", ai.get("confidence", 85.0)))
            
        # 2. OCR Quality Score
        text_len = len(c.get("extracted_text", ""))
        ocr_quality = 90.0 if text_len > 150 else 75.0 if text_len > 30 else 50.0
        
        # 3. Hospital Trust
        hosp_name = c.get("hospital", "")
        hosp_doc = hospital_dict.get(str(hosp_name).strip().lower())
        hospital_trust = 95.0 if hosp_doc and hosp_doc.get("network") else 55.0
        
        # 4. Duplicate Probability Inverse (Trust component)
        # If image hash is unique, duplicate probability is low. If duplicated, trust is compromised.
        image_hash = c.get("image_hash")
        dup_count = 0
        if image_hash:
            dup_count = claims_collection.count_documents({"image_hash": image_hash})
        duplicate_prob_inverse = 100.0 if dup_count <= 1 else 15.0
        
        # 5. Government Employee Verification
        user_doc = mobile_to_user.get(mob)
        phone_digits = "".join(ch for ch in str(mob or "") if ch.isdigit())
        is_govt_verified = any(
            "".join(ch for ch in str(g.get("auth", {}).get("phone") or g.get("mobile") or g.get("phone") or "") if ch.isdigit()) == phone_digits 
            for g in govt_employees
        ) or (user_doc and user_doc.get("is_government_employee"))
        govt_verified = 100.0 if is_govt_verified else 40.0
        
        components = trust_result.get("components") or {}
        contributions = trust_result.get("contributions") or {}
        trust_score = trust_result.get("score")
        if trust_score is None:
            contributions = {
                "ai_confidence": ai_confidence * 0.30,
                "ocr_confidence": ocr_quality * 0.20,
                "hospital_trust": hospital_trust * 0.20,
                "duplicate_prob_inverse": duplicate_prob_inverse * 0.20,
                "govt_verified": govt_verified * 0.10,
            }
            trust_score = sum(contributions.values())
        if components:
            ai_confidence = components.get("ai_confidence", ai_confidence)
            ocr_quality = components.get("ocr_confidence", ocr_quality)
            hospital_trust = components.get("hospital_trust", hospital_trust)
            duplicate_prob_inverse = components.get("duplicate_prob_inverse", duplicate_prob_inverse)
            govt_verified = components.get("govt_verified", govt_verified)
        
        # Determine Color Category
        if trust_score >= 85:
            color = "Green"
            risk_level = "Secure"
            badge = "badge-approved"
        elif trust_score >= 60:
            color = "Orange"
            risk_level = "Caution"
            badge = "badge-pending"
        else:
            color = "Red"
            risk_level = "High Risk"
            badge = "badge-rejected"
            
        # Build logical reasoning details
        reasoning = []
        if is_govt_verified:
            reasoning.append("Claimant profile successfully verified against official Government Employee Database.")
        else:
            reasoning.append("Claimant profile unverified in Government Employee list; proceed with caution.")
            
        if hosp_doc and hosp_doc.get("network"):
            reasoning.append("Treatment taken at In-Network hospital. Facility credentialed.")
        else:
            reasoning.append("Treatment taken at Out-of-Network hospital. Facility requires manual verification.")
            
        if dup_count > 1:
            reasoning.append(f"CRITICAL: Bill document duplicate detected! Matches {dup_count - 1} other claim(s) in system.")
        else:
            reasoning.append("Bill document hash is unique. No immediate duplicates detected.")
            
        if isinstance(ai, dict) and ai.get("fraud_flags"):
            reasoning.append(f"AI flags raised: {', '.join(ai.get('fraud_flags'))}")
            
        analyzed_claims.append({
            "claim_id": c.get("claim_id"),
            "mobile": mob,
            "claimant_name": c.get("name"),
            "amount": c.get("amount"),
            "hospital": hosp_name,
            "date": c.get("date") or c.get("created_at"),
            "trust_score": round(trust_score, 1),
            "ai_confidence": round(ai_confidence, 1),
            "ocr_quality": round(ocr_quality, 1),
            "hospital_trust": round(hospital_trust, 1),
            "duplicate_prob_inverse": round(duplicate_prob_inverse, 1),
            "govt_verified": round(govt_verified, 1),
            "color": color,
            "risk_level": risk_level,
            "badge_class": badge,
            "reasoning": reasoning,
            "contributions": contributions,
            "remarks": c.get("officer_remarks", c.get("remarks", "No officer remarks recorded."))
        })
        
    return render_template("admin_trust_analysis.html", claims=analyzed_claims)


# ==========================================
# PHASE 8 - DATABASE VIEWER MODULE
# ==========================================

@admin_bp.route("/db_explorer")
@role_required("admin")
def database_explorer():
    from database.mongo_client import db
    import math
    
    collection_name = request.args.get('collection', 'users').strip()
    search_query = request.args.get('q', '').strip()
    sort_by = request.args.get('sort', '_id').strip()
    sort_order = int(request.args.get('order', '-1'))
    page = int(request.args.get('page', '1'))
    limit = 10
    
    valid_collections = ['users', 'claims', 'hospitals', 'govtlist', 'govtofficers', 'admins', 'documents']
    if collection_name not in valid_collections:
        collection_name = 'users'
        
    target_col = db[collection_name]
    
    # 1. Build Query
    find_query = {}
    if search_query:
        # Search all string fields (regex) or exact match for objectids/numbers
        search_regex = {"$regex": search_query, "$options": "i"}
        # Attempt to inspect key elements
        sample_doc = target_col.find_one()
        if sample_doc:
            or_conditions = []
            for k in sample_doc.keys():
                if isinstance(sample_doc[k], str):
                    or_conditions.append({k: search_regex})
                elif k in ['mobile', 'phone', 'phone_number', 'ppo_number', 'claim_id', 'officer_id', 'email']:
                    or_conditions.append({k: search_query})
            if or_conditions:
                find_query["$or"] = or_conditions
                
    # 2. Count Total Records
    total_records = target_col.count_documents(find_query)
    total_pages = math.ceil(total_records / limit) if total_records > 0 else 1
    
    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages
        
    skip = (page - 1) * limit
    
    # 3. Retrieve Records
    records = list(target_col.find(find_query).sort(sort_by, sort_order).skip(skip).limit(limit))
    
    # Formatting helper to convert ObjectIds to string for JSON viewer
    from bson import ObjectId
    import json
    
    formatted_records = []
    for r in records:
        clean_r = mask_document(dict(r))
        for k, v in clean_r.items():
            if isinstance(v, ObjectId):
                clean_r[k] = str(v)
            elif isinstance(v, bytes):
                clean_r[k] = str(v) # Hashed password bytes representation
        formatted_records.append({
            "doc": clean_r,
            "json_str": json.dumps(clean_r, indent=2)
        })
        
    return render_template(
        "admin_db_explorer.html",
        collections=valid_collections,
        active_collection=collection_name,
        records=formatted_records,
        search_query=search_query,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        total_pages=total_pages,
        total_records=total_records
    )

@admin_bp.route("/db_explorer/export/<collection>")
@role_required("admin")
def db_explorer_export(collection):
    from database.mongo_client import db
    import csv
    import io
    from flask import make_response
    from bson import ObjectId
    
    valid_collections = ['users', 'claims', 'hospitals', 'govtlist', 'govtofficers', 'admins', 'documents']
    if collection not in valid_collections:
        flash("Invalid collection selection for export.", "danger")
        return redirect(url_for('admin.database_explorer'))
        
    target_col = db[collection]
    records = list(target_col.find())
    
    if not records:
        flash("No records found in this collection to export.", "warning")
        return redirect(url_for('admin.database_explorer', collection=collection))
        
    # Headers are derived from union of keys in all records
    headers = set()
    for r in records:
        headers.update(r.keys())
    headers = sorted(list(headers))
    
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(headers)
    
    masked_export = request.args.get("masked", "1") != "0"
    log_audit(session.get("user_id", "admin"), "db_export", f"Exported {collection}", {"masked": masked_export})

    for r in records:
        row = []
        for h in headers:
            val = r.get(h, "")
            if masked_export:
                val = mask_value(h, val)
            if isinstance(val, ObjectId):
                val = str(val)
            elif isinstance(val, bytes):
                val = "<binary data>"
            row.append(val)
        cw.writerow(row)
        
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=export_{collection}.csv"
    output.headers["Content-type"] = "text/csv"
    return output
