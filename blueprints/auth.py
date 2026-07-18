from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import bcrypt
from flask import Blueprint, request, render_template, redirect, session, flash, url_for, make_response, jsonify
from services.otp_service import store_otp, verify_otp
from database.user_repository import get_user_by_mobile, get_user_by_email, create_user, update_password
from database.govt_repository import get_employee_by_mobile, get_employee_by_email, get_employee_by_ppo, verify_employee_identity, link_new_mobile
from database.mongo_client import admins_collection, users_collection, officers_collection, email_verifications_collection
from config.settings import Config
from services.auth_service import authenticate_admin, authenticate_officer, authenticate_user, fetch_user_role, needs_verification
from utils.logger import log_audit, logger
from utils.rate_limiter import limit_route
from utils.password_policy import validate_password_policy
from utils.jwt_utils import clear_auth_cookies, issue_auth_tokens, revoke_refresh_token, set_auth_cookies, get_token_from_request, decode_token, is_token_revoked

auth_bp = Blueprint('auth', __name__)


def _clear_identity_verification_state():
    session.pop("pending_mobile", None)
    session.pop("pending_role", None)
    session.pop("pending_identity_verification", None)
    session.pop("identity_verification_attempts", None)
    session.pop("pending_login_mode", None)
    session.pop("pending_login_phone", None)
    session.pop("pending_login_ppo", None)
    session.pop("pending_login_officer_id", None)


def _clear_authenticated_identity():
    for key in (
        "authenticated",
        "role",
        "mobile",
        "email",
        "officer_id",
        "government_verified",
        "verification_completed",
        "is_verified",
        "is_government_employee",
    ):
        session.pop(key, None)


def _normalize_mobile(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _now():
    return datetime.now(timezone.utc)


def _parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _pick_nested(document, *paths, default=None):
    current = document or {}
    for path in paths:
        node = current
        found = True
        for key in str(path).split("."):
            if not isinstance(node, dict) or key not in node:
                found = False
                break
            node = node.get(key)
        if found and node not in (None, ""):
            return node
    return default


def _government_identity_id(employee, mobile=None):
    employee = employee or {}
    return (
        _pick_nested(employee, "auth.ppoNumber", "auth.ppo_number")
        or _pick_nested(employee, "identity.aadhaarLast4", "identity.aadhaar_last4")
        or _pick_nested(employee, "identity.aadhaarNumber", "identity.aadhaar_number")
        or mobile
    )


def _collection_for_role(role):
    if role == "admin":
        return admins_collection
    if role == "officer":
        return officers_collection
    return users_collection


def _record_failed_login(account, role, actor, reason):
    log_audit(actor, "failed_authentication", reason, {"role": role})
    if not account or not account.get("_id"):
        return
    failures = int(account.get("failed_login_count", 0) or 0) + 1
    update = {
        "failed_login_count": failures,
        "last_failed_login_at": _now().isoformat(),
    }
    if failures >= 5:
        update["account_locked_until"] = (_now() + timedelta(minutes=15)).isoformat()
        log_audit(actor, "account_locked", "Account locked after repeated failed logins", {"role": role, "failed_count": failures})
    _collection_for_role(role).update_one({"_id": account["_id"]}, {"$set": update})


def _is_account_locked(account):
    locked_until = _parse_dt((account or {}).get("account_locked_until"))
    return bool(locked_until and locked_until > _now())


def _reset_failed_login(account, role):
    if account and account.get("_id"):
        _collection_for_role(role).update_one(
            {"_id": account["_id"]},
            {"$set": {"failed_login_count": 0}, "$unset": {"account_locked_until": "", "last_failed_login_at": ""}},
        )


def _verification_payload(employee):
    now = datetime.now(timezone.utc).isoformat()
    employee = employee or {}
    return {
        "is_verified": True,
        "government_verified": True,
        "verification_completed": True,
        "verification_date": now,
        "updated_at": now,
        "ppo_number": _pick_nested(employee, "auth.ppoNumber", "auth.ppo_number"),
        "name": employee.get("name") or _pick_nested(employee, "profile.fullName", "profile.full_name", "auth.name"),
        "email": employee.get("email") or _pick_nested(employee, "auth.email"),
        "department": employee.get("department") or _pick_nested(employee, "pension.retiredDepartment", "pension.department"),
        "designation": employee.get("designation") or _pick_nested(employee, "pension.designation"),
        "mobile_verified": True,
    }


def _officer_identity_filters(identifier, officer_doc=None):
    normalized_mobile = _normalize_mobile(identifier)
    filters = []
    if officer_doc:
        if officer_doc.get("officer_id"):
            filters.append({"officer_id": officer_doc.get("officer_id")})
        if officer_doc.get("employee_id"):
            filters.append({"employee_id": officer_doc.get("employee_id")})
        if officer_doc.get("email"):
            filters.append({"email": officer_doc.get("email").strip().lower()})
        if officer_doc.get("phone_number"):
            filters.append({"phone_number": _normalize_mobile(officer_doc.get("phone_number"))})
        if officer_doc.get("mobile"):
            filters.append({"mobile": _normalize_mobile(officer_doc.get("mobile"))})
        if officer_doc.get("phone"):
            filters.append({"phone": _normalize_mobile(officer_doc.get("phone"))})
    if normalized_mobile:
        filters.append({"mobile": normalized_mobile})
        filters.append({"phone": normalized_mobile})
        filters.append({"phone_number": normalized_mobile})
    if "@" in str(identifier or ""):
        filters.append({"email": str(identifier).strip().lower()})
    return filters


def _preserve_officer_fields(payload, officer_doc=None, employee=None):
    officer_doc = officer_doc or {}
    employee = employee or {}
    preserve_keys = [
        "officer_id",
        "employee_id",
        "employee_type",
        "experience_years",
        "gender",
        "age",
        "date_of_birth",
        "department",
        "designation",
        "salary",
        "blood_group",
        "marital_status",
        "address",
        "city",
        "district",
        "state",
        "pincode",
        "phone_number",
        "phone",
        "email",
        "aadhaar_number",
        "aadhaar_last4",
        "pan_last4",
        "nominee_name",
        "relationship",
        "insurance_provider",
        "policy_number",
        "policy_start",
        "policy_end",
        "claim_eligibility",
        "medical_history",
        "emergency_contact",
        "emergency_phone",
        "bank_name",
        "account_last4",
        "ifsc_code",
        "joining_date",
        "date_of_joining",
    ]
    for key in preserve_keys:
        value = officer_doc.get(key)
        if value in (None, ""):
            value = employee.get(key)
        if value not in (None, "") and payload.get(key) in (None, ""):
            payload[key] = value
    return payload


def _sync_officer_verification_state(identifier, employee, officer_doc=None):
    payload = _verification_payload(employee)
    normalized_mobile = _normalize_mobile(identifier)
    payload.update({
        "role": "officer",
        "status": "Active",
        "is_disabled": False,
        "is_deleted": False,
        "mobile": normalized_mobile,
        "phone": normalized_mobile,
        "phone_number": normalized_mobile,
    })
    if officer_doc:
        payload["officer_id"] = officer_doc.get("officer_id") or employee.get("employee_id")
        payload["employee_id"] = officer_doc.get("employee_id") or employee.get("employee_id") or payload["officer_id"]
        payload["name"] = officer_doc.get("name") or employee.get("name") or _pick_nested(employee, "profile.fullName", "profile.full_name")
        payload["email"] = officer_doc.get("email") or employee.get("email") or _pick_nested(employee, "auth.email")
        payload["department"] = officer_doc.get("department") or employee.get("department") or _pick_nested(employee, "pension.retiredDepartment", "pension.department")
        payload["designation"] = officer_doc.get("designation") or employee.get("designation") or _pick_nested(employee, "pension.designation")
    else:
        payload["officer_id"] = employee.get("employee_id") or normalized_mobile
        payload["employee_id"] = employee.get("employee_id") or payload["officer_id"]
        payload["name"] = employee.get("name") or _pick_nested(employee, "profile.fullName", "profile.full_name")
        payload["email"] = employee.get("email") or _pick_nested(employee, "auth.email")
        payload["department"] = employee.get("department") or _pick_nested(employee, "pension.retiredDepartment", "pension.department")
        payload["designation"] = employee.get("designation") or _pick_nested(employee, "pension.designation")

    existing_officer = officer_doc
    if not existing_officer:
        for query in _officer_identity_filters(identifier):
            existing_officer = officers_collection.find_one(query)
            if existing_officer:
                break

    if existing_officer and existing_officer.get("_id"):
        payload = _preserve_officer_fields(payload, officer_doc=existing_officer, employee=employee)
        officers_collection.update_one({"_id": existing_officer["_id"]}, {"$set": payload})
    else:
        try:
            payload = _preserve_officer_fields(payload, officer_doc=officer_doc, employee=employee)
            officers_collection.insert_one(payload.copy())
        except Exception:
            officers_collection.update_one(
                {"$or": [{"officer_id": payload.get("officer_id")}, {"employee_id": payload.get("employee_id")}, {"mobile": normalized_mobile}, {"phone": normalized_mobile}, {"phone_number": normalized_mobile}, {"email": payload.get("email")}]},
                {"$set": payload},
                upsert=True,
            )

    existing_user = None
    for query in _officer_identity_filters(identifier):
        existing_user = users_collection.find_one(query)
        if existing_user:
            break

    if existing_user and existing_user.get("_id"):
        payload = _preserve_officer_fields(payload, officer_doc=existing_user, employee=employee)
        users_collection.update_one({"_id": existing_user["_id"]}, {"$set": payload})
    else:
        user_payload = {
            "mobile": normalized_mobile,
            "phone": normalized_mobile,
            "phone_number": normalized_mobile,
            "email": payload.get("email"),
            "officer_id": payload.get("officer_id"),
            "employee_id": payload.get("employee_id"),
            "name": payload.get("name"),
            "department": payload.get("department"),
            "designation": payload.get("designation"),
            "role": "officer",
            **payload,
        }
        try:
            users_collection.insert_one(user_payload)
        except Exception:
            users_collection.update_one(
                {"$or": [{"mobile": normalized_mobile}, {"phone": normalized_mobile}, {"phone_number": normalized_mobile}, {"email": payload.get("email")}, {"officer_id": payload.get("officer_id")}, {"employee_id": payload.get("employee_id")}]},
                {"$set": user_payload},
                upsert=True,
            )

    logger.info("[VERIFY] Government verification completed | officer_id=%s", payload.get("officer_id"))
    return payload


def _create_officer_session(officer_doc, identifier):
    officer_doc = officer_doc or {}
    identifier_mobile = _normalize_mobile(identifier)
    document_mobile = _normalize_mobile(
        officer_doc.get("mobile")
        or officer_doc.get("phone")
        or officer_doc.get("phone_number")
    )
    mobile = identifier_mobile if len(identifier_mobile) >= 10 else document_mobile
    session["mobile"] = mobile
    session["user_id"] = officer_doc.get("officer_id") or mobile
    session["role"] = "officer"
    session["authenticated"] = True
    logger.info("[SESSION] Officer session created | officer_id=%s", session.get("user_id"))


def _start_identity_verification(mobile, role="user"):
    _clear_authenticated_identity()
    _clear_identity_verification_state()
    session["pending_mobile"] = mobile
    session["pending_role"] = role
    session["pending_identity_verification"] = True
    session["identity_verification_attempts"] = 0
    flash("Please verify your government identity to continue.", "info")
    return redirect(url_for("auth.verify_identity_page"))


def _start_pensioner_otp_flow(phone: str, ppo_number: str):
    _clear_identity_verification_state()
    session["pending_login_mode"] = "ppo_otp"
    session["pending_login_phone"] = _normalize_mobile(phone)
    session["pending_login_ppo"] = str(ppo_number or "").strip()
    otp = store_otp(session["pending_login_phone"])
    if Config.FLASK_ENV == "development":
        print(f"DEV OTP for {session['pending_login_phone']}: {otp}")
    flash("OTP sent to your registered mobile number.", "success")
    return render_template("otp_verify.html", mobile=session["pending_login_phone"])


def _start_officer_otp_flow(phone: str, officer_id: str):
    _clear_identity_verification_state()
    session["pending_login_mode"] = "officer_otp"
    session["pending_login_phone"] = _normalize_mobile(phone)
    session["pending_login_officer_id"] = str(officer_id or "").strip()
    otp = store_otp(session["pending_login_phone"])
    if Config.FLASK_ENV == "development":
        print(f"DEV OTP for {session['pending_login_phone']}: {otp}")
    flash("OTP sent to the officer's registered mobile number.", "success")
    return render_template("otp_verify.html", mobile=session["pending_login_phone"])


def _normalize_officer_mobile(officer_doc, provided_mobile=None):
    doc_mobile = _normalize_mobile(
        (officer_doc or {}).get("auth", {}).get("phone")
        or (officer_doc or {}).get("phone")
        or (officer_doc or {}).get("phone_number")
        or (officer_doc or {}).get("mobile")
    )
    provided_mobile = _normalize_mobile(provided_mobile)
    return provided_mobile or doc_mobile


def _resolve_officer_by_id_and_mobile(officer_id, mobile_number):
    officer = fetch_user_role(officer_id, preferred_role="officer")
    if not officer.get("found"):
        return None, "Officer record not found."
    officer_doc = officer.get("document") or {}
    official_mobile = _normalize_officer_mobile(officer_doc)
    provided_mobile = _normalize_mobile(mobile_number)
    if not official_mobile or not provided_mobile:
        return None, "Officer mobile number is required."
    if official_mobile != provided_mobile:
        return None, "Officer ID and mobile number do not match."
    return officer_doc, None


def _resolve_beneficiary_by_mobile_and_ppo(mobile_number, ppo_number):
    mobile = _normalize_mobile(mobile_number)
    ppo = str(ppo_number or "").strip()
    if not mobile or not ppo:
        return None, "Mobile number and PPO number are required."
    pensioner = get_employee_by_ppo(ppo)
    if not pensioner:
        return None, "PPO record not found."
    official_mobile = _normalize_mobile(
        pensioner.get("auth", {}).get("phone")
        or pensioner.get("phone")
        or pensioner.get("mobile")
    )
    if official_mobile != mobile:
        return None, "Mobile number and PPO number do not match."
    return pensioner, None


def _queue_email_verification(email: str, mobile: str | None = None):
    if not email:
        return None
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    email_verifications_collection.update_one(
        {"email": email.lower().strip()},
        {"$set": {
            "email": email.lower().strip(),
            "mobile": mobile,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "verified": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    logger.info("[AUTH] Email verification queued for %s", email)
    return raw_token


def _finalize_session_login(role: str, identifier: str, account: dict, redirect_endpoint: str):
    session.permanent = True
    session["authenticated"] = True
    session["role"] = role
    tokens = issue_auth_tokens(identifier, role, extra={"mobile": account.get("mobile", identifier), "user_id": _government_identity_id(account, identifier)})
    response = make_response(redirect(url_for(redirect_endpoint)))
    set_auth_cookies(response, tokens)
    return response

@auth_bp.route("/")
def login():
    _clear_identity_verification_state()
    if session.get("authenticated") and (session.get("mobile") or session.get("user_id")):
        role = str(session.get("role", "user")).strip().lower()
        return redirect(url_for(f'{role}.dashboard' if role != 'user' else 'user.dashboard'))
    return render_template("login.html")

@auth_bp.route("/send_otp", methods=["POST"])
@limit_route("5 per minute", redirect_endpoint="auth.login", message="Too many OTP requests. Please wait a moment.")
def send_otp_route():
    ppo_number = request.form.get("ppo_number") or request.form.get("ppoNumber")
    officer_id = request.form.get("officer_id") or request.form.get("officerId")
    mobile_number = request.form.get("mobile_number") or request.form.get("mobile") or request.form.get("phone")

    if officer_id:
        officer_doc, error = _resolve_officer_by_id_and_mobile(officer_id, mobile_number)
        if error:
            flash(error, "danger")
            return redirect(url_for("auth.login"))
        return _start_officer_otp_flow(_normalize_officer_mobile(officer_doc, mobile_number), officer_id)

    if ppo_number:
        pensioner, error = _resolve_beneficiary_by_mobile_and_ppo(mobile_number, ppo_number)
        if error:
            flash(error, "danger")
            return redirect(url_for('auth.login'))
        return _start_pensioner_otp_flow(_normalize_mobile(mobile_number), ppo_number)

    flash("PPO number or officer ID is required", "danger")
    return redirect(url_for('auth.login'))

@auth_bp.route("/verify_otp", methods=["POST"])
@limit_route("5 per minute", redirect_endpoint="auth.login", message="Too many OTP attempts. Please wait a moment.")
def verify_otp_route():
    mobile = request.form.get("mobile")
    otp = request.form.get("otp")

    is_valid, message = verify_otp(mobile, otp)
    if is_valid:
        if session.get("pending_login_mode") == "ppo_otp":
            ppo_number = session.get("pending_login_ppo")
            pensioner = get_employee_by_ppo(ppo_number) or get_employee_by_mobile(mobile)
            if not pensioner:
                flash("PPO record not found. Please log in again.", "danger")
                return redirect(url_for("auth.login"))
            session["mobile"] = mobile
            session["user_id"] = _government_identity_id(pensioner, mobile)
            session["role"] = "user"
            session["authenticated"] = True
            tokens = issue_auth_tokens(session["user_id"], "user", extra={"mobile": mobile, "user_id": session["user_id"], "ppo_number": ppo_number})
            response = make_response(redirect(url_for("user.dashboard")))
            set_auth_cookies(response, tokens)
            _clear_identity_verification_state()
            log_audit(mobile, "login", "PPO OTP login successful. Role: user")
            flash("Logged in successfully!", "success")
            return response

        if session.get("pending_login_mode") == "officer_otp":
            officer_id = session.get("pending_login_officer_id")
            resolved = fetch_user_role(officer_id, preferred_role="officer")
            officer_doc = resolved.get("document") or {}
            if not officer_doc:
                flash("Officer record not found. Please log in again.", "danger")
                return redirect(url_for("auth.login"))
            session["mobile"] = mobile
            session["user_id"] = officer_doc.get("officer_id") or officer_id or mobile
            session["officer_name"] = officer_doc.get("name") or _pick_nested(officer_doc, "profile.fullName", "profile.full_name") or "Claims Officer"
            session["role"] = "officer"
            session["authenticated"] = True
            tokens = issue_auth_tokens(session["user_id"], "officer", extra={"mobile": mobile, "user_id": session["user_id"], "officer_id": officer_id})
            response = make_response(redirect(url_for("officer.dashboard")))
            set_auth_cookies(response, tokens)
            _clear_identity_verification_state()
            log_audit(mobile, "login", "Officer OTP login successful. Role: officer")
            flash("Logged in successfully!", "success")
            return response

        resolved = fetch_user_role(mobile)
        if not resolved.get("found"):
            flash("User not found. Please register first.", "danger")
            return redirect(url_for('auth.login'))

        role = str(resolved.get("role") or "user").lower()
        account = resolved.get("document") or {}
        log_audit(mobile, "login", f"OTP login successful. Role: {role}")

        if role == "admin":
            _clear_authenticated_identity()
            session["mobile"] = account.get("email", mobile)
            session["user_id"] = account.get("email", mobile)
            session["role"] = "admin"
            flash("Logged in successfully!", "success")
            logger.info("[AUTH] Admin login success | user_id=%s", session.get("user_id"))
            return _finalize_session_login("admin", account.get("email", mobile), account, "admin.dashboard")

        if role == "officer":
            if needs_verification(account):
                logger.info("[LOOP PREVENTION] Verification required once for officer | identifier=%s", mobile)
                return _start_identity_verification(mobile, "officer")
            _clear_identity_verification_state()
            _clear_authenticated_identity()
            _create_officer_session(account, mobile)
            flash("Logged in successfully!", "success")
            logger.info("[AUTH] Officer login success | user_id=%s", session.get("user_id"))
            logger.info("[LOOP PREVENTION] Verification bypassed for verified officer | user_id=%s", session.get("user_id"))
            return _finalize_session_login("officer", mobile, account, "officer.dashboard")

        employee = account if account.get("is_government_employee") else get_employee_by_mobile(mobile)
        if employee or account.get("is_government_employee"):
            _clear_authenticated_identity()
            session["mobile"] = mobile
            session["user_id"] = _government_identity_id(employee, mobile)
            session["role"] = role
            flash("Logged in successfully!", "success")
            logger.info("[AUTH] User login success | mobile=%s", mobile)
            return _finalize_session_login("user", mobile, employee or account, "user.dashboard")

        return _start_identity_verification(mobile, role)

    flash(message, "danger")
    return render_template("otp_verify.html", mobile=mobile)

@auth_bp.route("/register")
def register_page():
    return redirect(url_for("auth.request_access_page"))

@auth_bp.route("/register_user", methods=["POST"])
def register_submit():
    return redirect(url_for("auth.request_access_page"))


@auth_bp.route("/request-access")
def request_access_page():
    return render_template("request_access.html")


@auth_bp.route("/request-access", methods=["POST"])
def request_access_submit():
    from database.mongo_client import db as mongo_db

    payload = {
        "requested_role": request.form.get("requested_role", "user"),
        "full_name": request.form.get("full_name", "").strip(),
        "email": request.form.get("email", "").strip().lower(),
        "phone": _normalize_mobile(request.form.get("phone")),
        "ppo_number": request.form.get("ppo_number", "").strip(),
        "officer_id": request.form.get("officer_id", "").strip(),
        "department": request.form.get("department", "").strip(),
        "aadhaar_number": _normalize_mobile(request.form.get("aadhaar_number")),
        "designation": request.form.get("designation", "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "Pending",
    }
    if not payload["full_name"]:
        flash("Full name is required.", "danger")
        return redirect(url_for("auth.request_access_page"))
    mongo_db["access_requests"].insert_one(payload)
    flash("Your access request has been sent to the admin team.", "success")
    return redirect(url_for("auth.login"))

@auth_bp.route("/login_user", methods=["POST"])
@limit_route("10 per minute", redirect_endpoint="auth.login", message="Too many login attempts. Please try again later.")
def login_submit():
    submitted_role = str(request.form.get("role", "user")).strip().lower()
    password = request.form.get("password")
    ppo_number = request.form.get("ppo_number") or request.form.get("ppoNumber")
    mobile_number = request.form.get("mobile_number") or request.form.get("mobile") or request.form.get("phone")

    # Admin submits field name 'email'; Officer and Beneficiary submit 'identifier'
    # (set dynamically by handleRoleChange in the frontend).
    if submitted_role == "admin":
        identifier = str(request.form.get("email") or "").strip().lower()
    else:
        identifier = str(
            request.form.get("identifier")
            or request.form.get("email")
            or request.form.get("mobile_number")
            or ""
        ).strip()

    # ------------------------------------------------------------------ #
    # Beneficiary OTP path: PPO + Mobile → send OTP (no password needed) #
    # ------------------------------------------------------------------ #
    if ppo_number:
        pensioner, error = _resolve_beneficiary_by_mobile_and_ppo(mobile_number, ppo_number)
        if error:
            flash(error, "danger")
            return redirect(url_for("auth.login"))
        return _start_pensioner_otp_flow(_normalize_mobile(mobile_number), ppo_number)

    # ------------------------------------------------------------------ #
    # Password login path (Admin / Officer / Beneficiary)                #
    # ------------------------------------------------------------------ #
    if not identifier:
        flash("Please enter your email or mobile number.", "danger")
        return redirect(url_for("auth.login"))

    resolved = fetch_user_role(identifier, preferred_role=submitted_role)
    if not resolved.get("found"):
        if "@" in identifier:
            flash("Email not found. Please use your registered email.", "danger")
        else:
            log_audit(identifier, "failed_authentication", "Login attempted for unknown account", {"submitted_role": submitted_role})
            flash("Invalid credentials", "danger")
        return redirect(url_for("auth.login"))

    role = str(resolved.get("role") or "user").strip().lower()
    account = resolved.get("document") or {}

    if submitted_role and submitted_role != role:
        log_audit(identifier, "security_violation", "Login role mismatch rejected", {"submitted_role": submitted_role, "stored_role": role})
        flash("Invalid credentials", "danger")
        return redirect(url_for("auth.login"))

    if _is_account_locked(account):
        log_audit(identifier, "failed_authentication", "Login attempted while account is locked", {"role": role})
        flash("Account temporarily locked. Please try again after 15 minutes.", "danger")
        return redirect(url_for("auth.login"))

    if role == "admin":
        auth_result = authenticate_admin(identifier, password)
    elif role == "officer":
        auth_result = authenticate_officer(identifier, password)
    else:
        auth_result = authenticate_user(identifier, password)

    if not auth_result.get("ok"):
        if auth_result.get("requires_identity_verification"):
            return _start_identity_verification(identifier, role)
        _record_failed_login(account, role, identifier, auth_result.get("reason", "Invalid credentials"))
        flash(auth_result.get("reason", "Invalid credentials"), "danger")
        return redirect(url_for('auth.login'))

    _reset_failed_login(account, role)
    _clear_identity_verification_state()
    _clear_authenticated_identity()
    log_audit(identifier, "login", f"Password login successful. Role: {role}")
    flash("Logged in successfully!", "success")

    if role == "admin":
        admin = auth_result.get("document") or {}
        session["mobile"] = admin.get("email", identifier)
        session["user_id"] = admin.get("email", identifier)
        session["role"] = "admin"
        return _finalize_session_login("admin", admin.get("email", identifier), admin, "admin.dashboard")

    if role == "officer":
        officer = auth_result.get("document") or {}
        if needs_verification(officer):
            return _start_identity_verification(identifier, "officer")
        _create_officer_session(officer, identifier)
        return _finalize_session_login("officer", identifier, officer, "officer.dashboard")

    user = auth_result.get("document") or {}
    employee = auth_result.get("employee") or (user if user.get("is_government_employee") else get_employee_by_mobile(identifier))
    if employee or user.get("is_government_employee"):
        session["mobile"] = _normalize_mobile(identifier)
        session["user_id"] = employee.get("employee_id") if employee else _normalize_mobile(identifier)
        session["role"] = "user"
        return _finalize_session_login("user", identifier, employee or user, "user.dashboard")

    return _start_identity_verification(identifier, "user")

@auth_bp.route("/forgot_password")
def forgot_password():
    return render_template("forgot_password.html")

@auth_bp.route("/send_reset_otp", methods=["POST"])
@limit_route("5 per minute", redirect_endpoint="auth.forgot_password", message="Too many reset requests. Please wait a moment.")
def reset_otp_request():
    mobile = request.form.get("mobile")
    if not (get_user_by_mobile(mobile) or get_user_by_email(mobile) or get_employee_by_mobile(mobile) or get_employee_by_email(mobile)):
        flash("Mobile number not registered", "danger")
        return redirect(url_for('auth.forgot_password'))
        
    otp = store_otp(mobile)
    if Config.FLASK_ENV == "development":
        print(f"DEV RESET OTP for {mobile}: {otp}")
        
    return render_template("reset_password.html", mobile=mobile)

@auth_bp.route("/reset_password", methods=["POST"])
@limit_route("5 per minute", redirect_endpoint="auth.forgot_password", message="Too many reset attempts. Please wait a moment.")
def reset_password_submit():
    mobile = request.form.get("mobile")
    otp = request.form.get("otp")
    new_password = request.form.get("password")

    is_valid, message = verify_otp(mobile, otp)
    if is_valid:
        ok, errors = validate_password_policy(new_password)
        if not ok:
            flash(" ".join(errors), "danger")
            return render_template("reset_password.html", mobile=mobile)
        hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt())
        user = get_user_by_mobile(mobile) or get_user_by_email(mobile)
        officer = get_employee_by_mobile(mobile) or get_employee_by_email(mobile)
        if user and user.get("_id"):
            users_collection.update_one(
                {"_id": user["_id"]},
                {"$set": {"auth.passwordHash": hashed, "updated_at": datetime.now(timezone.utc).isoformat()}}
            )
        if officer and officer.get("auth"):
            if officer.get("role") == "officer":
                officers_collection.update_one(
                    {"_id": officer["_id"]},
                    {"$set": {"auth.passwordHash": hashed, "updatedAt": datetime.now(timezone.utc).isoformat()}}
                )
            else:
                update_password(mobile, hashed)
        log_audit(mobile, "password_reset", "User reset password via OTP")
        flash("Password updated successfully.", "success")
        return redirect(url_for('auth.login'))

    flash(message, "danger")
    return render_template("reset_password.html", mobile=mobile)

@auth_bp.route("/verify_identity")
def verify_identity_page():
    if not session.get("pending_identity_verification") or not session.get("pending_mobile"):
        flash("Please log in to continue.", "warning")
        return redirect(url_for("auth.login"))

    return render_template(
        "verify_identity.html",
        mobile=session.get("pending_mobile")
    )

@auth_bp.route("/verify_identity", methods=["POST"])
@limit_route("5 per minute", redirect_endpoint="auth.login", message="Too many verification attempts. Please wait a moment.")
def verify_identity_submit():
    pending_mobile = session.get("pending_mobile")
    if not session.get("pending_identity_verification") or not pending_mobile:
        flash("Please log in to continue.", "warning")
        return redirect(url_for("auth.login"))

    attempts = int(session.get("identity_verification_attempts", 0))
    if attempts >= 5:
        log_audit(pending_mobile, "identity_verification_failed", "Maximum verification attempts exceeded")
        _clear_identity_verification_state()
        flash("Identity verification failed.", "danger")
        return redirect(url_for("auth.login"))

    identity_data = {
        "aadhaar_number": request.form.get("aadhaar_number"),
        "ppo_number": request.form.get("ppo_number") or request.form.get("ppoNumber"),
        "full_name": request.form.get("full_name"),
        "department": request.form.get("department"),
        "mobile": pending_mobile,
    }

    is_verified, employee, message = verify_employee_identity(identity_data)
    if is_verified and employee:
        try:
            link_new_mobile(employee.get("employee_id") or employee.get("officer_id") or employee.get("aadhaar_number") or employee.get("identity", {}).get("aadhaarLast4"), pending_mobile)
        except Exception as exc:
            log_audit(pending_mobile, "identity_verification_failed", f"Failed to link mobile number: {str(exc)}")
            flash("Identity verification failed.", "danger")
            return render_template("verify_identity.html", mobile=pending_mobile)

        pending_role = str(session.get("pending_role", employee.get("role", "user"))).strip().lower()
        verification_ts = datetime.now(timezone.utc).isoformat()
        verification_flags = {
            "is_verified": True,
            "government_verified": True,
            "verification_completed": True,
            "verification_date": verification_ts,
            "updated_at": verification_ts,
        }

        if pending_role == "officer":
            officer_doc = fetch_user_role(pending_mobile, preferred_role="officer").get("document")
            synced = _sync_officer_verification_state(pending_mobile, employee, officer_doc=officer_doc)
            _clear_identity_verification_state()
            _clear_authenticated_identity()
            _create_officer_session(synced, pending_mobile)
            flash("Identity verified successfully. Welcome back!", "success")
            log_audit(pending_mobile, "identity_verified", f"Government identity verified for officer_id={session.get('user_id')}")
            logger.info("[AUTH] Officer login success | user_id=%s", session.get("user_id"))
            logger.info("[SESSION] Officer session created | user_id=%s", session.get("user_id"))
            return redirect(url_for('officer.dashboard'))

        user_query = {
            "$or": [
                {"mobile": pending_mobile},
                {"phone": pending_mobile},
                {"phone_number": pending_mobile},
            ]
        }
        existing_user = users_collection.find_one(user_query)
        if existing_user and existing_user.get("_id"):
            users_collection.update_one(
                {"_id": existing_user["_id"]},
                {"$set": {
                    "ppo_number": employee.get("auth", {}).get("ppoNumber") or employee.get("ppo_number"),
                    "department": employee.get("department") or _pick_nested(employee, "pension.retiredDepartment", "pension.department"),
                    "designation": employee.get("designation") or _pick_nested(employee, "pension.designation"),
                    "policy_number": employee.get("policy_number"),
                    "insurance_provider": employee.get("insurance_provider"),
                    "policy_start": employee.get("policy_start"),
                    "policy_end": employee.get("policy_end"),
                    "claim_eligibility": employee.get("claim_eligibility"),
                    "is_government_employee": True,
                    "name": employee.get("name") or _pick_nested(employee, "profile.fullName", "profile.full_name"),
                    "gender": employee.get("gender"),
                    "age": employee.get("age"),
                    "date_of_birth": employee.get("date_of_birth") or _pick_nested(employee, "profile.dob"),
                    "experience_years": employee.get("experience_years"),
                    "date_of_joining": employee.get("date_of_joining"),
                    "salary": employee.get("salary"),
                    "blood_group": employee.get("blood_group"),
                    "marital_status": employee.get("marital_status"),
                    "address": employee.get("address"),
                    "city": employee.get("city"),
                    "district": employee.get("district"),
                    "state": employee.get("state"),
                    "pincode": employee.get("pincode"),
                    "email": employee.get("email"),
                    "aadhaar_last4": employee.get("aadhaar_last4") or _pick_nested(employee, "identity.aadhaarLast4", "identity.aadhaar_last4"),
                    "pan_last4": employee.get("pan_last4"),
                    "nominee_name": employee.get("nominee_name"),
                    "relationship": employee.get("relationship"),
                    "medical_history": employee.get("medical_history"),
                    "emergency_contact": employee.get("emergency_contact"),
                    "emergency_phone": employee.get("emergency_phone"),
                    "bank_name": employee.get("bank_name"),
                    "account_last4": employee.get("account_last4"),
                    "ifsc_code": employee.get("ifsc_code"),
                    **verification_flags,
                }}
            )
        else:
            users_collection.update_one(
                {"mobile": pending_mobile},
                {"$set": {
                    "mobile": pending_mobile,
                    "role": "user",
                    "ppo_number": employee.get("auth", {}).get("ppoNumber") or employee.get("ppo_number"),
                    "department": employee.get("department") or _pick_nested(employee, "pension.retiredDepartment", "pension.department"),
                    "designation": employee.get("designation") or _pick_nested(employee, "pension.designation"),
                    "is_government_employee": True,
                    "name": employee.get("name") or _pick_nested(employee, "profile.fullName", "profile.full_name"),
                    "email": employee.get("email") or _pick_nested(employee, "auth.email"),
                    **verification_flags,
                }},
                upsert=True
            )

        resolved_role = pending_role
        _clear_identity_verification_state()
        _clear_authenticated_identity()
        session["mobile"] = pending_mobile
        session["user_id"] = _government_identity_id(employee, pending_mobile)
        session["role"] = resolved_role
        session["authenticated"] = True

        log_audit(pending_mobile, "identity_verified", f"Government identity verified for ppo_number={_pick_nested(employee, 'auth.ppoNumber', 'auth.ppo_number')}")
        
        # Auto-generate e-card for the newly verified beneficiary
        try:
            verified_user = users_collection.find_one({"mobile": pending_mobile}) or employee
            if verified_user:
                from services.ecard_generator import generate_and_save_ecard
                generate_and_save_ecard(pending_mobile, verified_user)
        except Exception as e:
            logger.warning(f"[AuthVerification] Auto e-card generation failed: {e}")

        flash("Identity verified successfully. Welcome back!", "success")
        logger.info("[AUTH] User login success | mobile=%s", pending_mobile)
        return redirect(url_for('user.dashboard'))

    attempts += 1
    session["identity_verification_attempts"] = attempts
    log_audit(
        pending_mobile,
        "identity_verification_failed",
        f"Identity verification failed (attempt {attempts}/5): {message}"
    )

    if "not found" in str(message).lower():
        flash("Government identity not found", "danger")
    else:
        flash("Identity verification failed.", "danger")

    if attempts >= 5:
        _clear_identity_verification_state()
        flash("Identity verification failed.", "danger")
        return redirect(url_for("auth.login"))

    return render_template("verify_identity.html", mobile=pending_mobile)

@auth_bp.route("/logout")
def logout():
    actor = session.get("user_id", session.get("mobile", "unknown"))
    log_audit(actor, "logout", "User logged out")
    response = make_response(redirect(url_for('auth.login')))
    _clear_identity_verification_state()
    session.clear()
    flash("You have been logged out.", "success")
    clear_auth_cookies(response)
    return response


@auth_bp.route("/verify_email/<token>")
def verify_email(token):
    token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    record = email_verifications_collection.find_one({"token_hash": token_hash})
    if not record:
        flash("Email verification link is invalid or expired.", "danger")
        return redirect(url_for("auth.login"))

    if record.get("expires_at"):
        try:
            expires_at = datetime.fromisoformat(str(record["expires_at"]).replace("Z", "+00:00"))
            if expires_at < datetime.now(timezone.utc):
                flash("Email verification link has expired.", "danger")
                return redirect(url_for("auth.login"))
        except Exception:
            pass

    email = record.get("email")
    mobile = record.get("mobile")
    users_collection.update_one(
        {"$or": [{"email": email}, {"mobile": mobile}]},
        {"$set": {"email_verified": True, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    email_verifications_collection.delete_one({"token_hash": token_hash})
    log_audit(email or mobile or "unknown", "email_verified", "Email address verified")
    flash("Email verified successfully.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/token/refresh", methods=["POST"])
def refresh_token_route():
    token = get_token_from_request(cookie_name=Config.JWT_REFRESH_COOKIE_NAME)
    if not token:
        return {"error": "missing_refresh_token", "message": "Refresh token is required."}, 401
    if is_token_revoked(token):
        return {"error": "revoked_token", "message": "Refresh token has been revoked."}, 401
    try:
        payload = decode_token(token)
    except Exception:
        return {"error": "invalid_token", "message": "Refresh token is invalid or expired."}, 401
    if payload.get("type") != "refresh":
        return {"error": "invalid_token_type", "message": "Refresh token required."}, 401

    role = str(payload.get("role") or "user")
    identity = str(payload.get("sub") or "")
    revoke_refresh_token(token, reason="rotation")
    tokens = issue_auth_tokens(identity, role, extra={"mobile": payload.get("mobile"), "user_id": payload.get("user_id")})
    flask_response = make_response(jsonify({"access_token": tokens["access_token"], "token_type": "bearer"}), 200)
    return set_auth_cookies(flask_response, tokens)
