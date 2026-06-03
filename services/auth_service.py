from __future__ import annotations

import bcrypt
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from database.admin_repository import get_admin_by_email
from database.govt_repository import get_employee_by_email, get_employee_by_mobile
from database.government_officer_repository import get_officer_by_identifier
from database.mongo_client import users_collection
from database.user_repository import get_user_by_mobile


ACTIVE_STATUSES = {"active", "enabled", "verified", "approved", "linked"}
INACTIVE_STATUSES = {"inactive", "disabled", "deleted", "removed", "deactivated", "suspended"}


def _normalize_identifier(identifier: Any) -> str:
    value = str(identifier or "").strip()
    if "@" in value:
        return value.lower()
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or value.lower()


def _digit_query_values(value: Any) -> list[Any]:
    normalized = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not normalized:
        return []
    values: list[Any] = [normalized]
    try:
        values.append(int(normalized))
    except ValueError:
        pass
    return values


def _normalize_role(role: Any) -> str:
    value = str(role or "").strip().lower()
    if value in {"beneficiary", "member"}:
        return "user"
    return value


def _normalize_password(password: Any) -> bytes:
    if isinstance(password, bytes):
        return password
    return str(password or "").encode("utf-8")


def _check_password(password: Any, stored_password: Any) -> bool:
    if stored_password in (None, ""):
        return False

    hashed = stored_password if isinstance(stored_password, bytes) else str(stored_password).encode("utf-8")
    try:
        return bcrypt.checkpw(_normalize_password(password), hashed)
    except Exception:
        return False


def _identifier_queries(identifier: str) -> Dict[str, Any]:
    normalized = _normalize_identifier(identifier)
    if not normalized:
        return {}

    if "@" in normalized:
        return {"email": normalized}

    digit_values = _digit_query_values(normalized)
    return {
        "$or": [
            {"mobile": normalized},
            {"phone": normalized},
            {"phone_number": normalized},
            {"mobile": {"$in": digit_values}},
            {"phone": {"$in": digit_values}},
            {"phone_number": {"$in": digit_values}},
            {"employee_id": normalized},
            {"officer_id": normalized},
        ]
    }


def _is_deleted(document: Dict[str, Any]) -> bool:
    return bool(
        document.get("deleted")
        or document.get("is_deleted")
        or document.get("deleted_at")
        or document.get("removed_at")
    )


def _status_value(document: Dict[str, Any]) -> str:
    for key in ("status", "account_status", "state"):
        value = document.get(key)
        if value:
            return str(value).strip().lower()
    if document.get("is_disabled"):
        return "disabled"
    if document.get("active") is False:
        return "inactive"
    return "active"


def verify_account_status(account_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not account_doc:
        return {
            "active": False,
            "reason": "Account not found.",
            "status": "missing",
        }

    status = _status_value(account_doc)
    locked_until = account_doc.get("account_locked_until")
    if locked_until:
        try:
            lock_dt = datetime.fromisoformat(str(locked_until).replace("Z", "+00:00"))
            if lock_dt > datetime.now(timezone.utc):
                return {
                    "active": False,
                    "reason": "Account is temporarily locked.",
                    "status": "locked",
                }
        except Exception:
            pass
    if _is_deleted(account_doc):
        return {
            "active": False,
            "reason": "Account has been deleted.",
            "status": status,
        }

    if status in INACTIVE_STATUSES:
        return {
            "active": False,
            "reason": "Account is disabled.",
            "status": status,
        }

    if status and status not in ACTIVE_STATUSES:
        return {
            "active": False,
            "reason": "Account is not active.",
            "status": status,
        }

    if account_doc.get("role") == "officer":
        has_officer_id = any(account_doc.get(key) for key in ("officer_id", "employee_id"))
        has_phone = any(account_doc.get(key) for key in ("phone", "phone_number", "mobile"))
        if not has_officer_id and not has_phone:
            return {
                "active": False,
                "reason": "Officer record is incomplete.",
                "status": status,
            }

    if account_doc.get("role") == "admin" and not account_doc.get("email"):
        return {
            "active": False,
            "reason": "Admin record is incomplete.",
            "status": status,
        }

    return {
        "active": True,
        "reason": "Account is active.",
        "status": status or "active",
    }


def needs_verification(user: Optional[Dict[str, Any]]) -> bool:
    if not user:
        return True

    if user.get("officer_id") and verify_account_status(user).get("active"):
        return False

    if str(user.get("role", "")).strip().lower() == "officer" and verify_account_status(user).get("active"):
        return False

    if user.get("government_verified") is True:
        return False

    if user.get("verification_completed") is True:
        return False

    if user.get("is_verified") is True:
        return False

    return True


def _resolve_employee_record(identifier: str, user_doc: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    candidates = [identifier]
    if user_doc:
        if user_doc.get("employee_id"):
            candidates.insert(0, user_doc.get("employee_id"))
        if user_doc.get("mobile"):
            candidates.append(user_doc.get("mobile"))
        if user_doc.get("email"):
            candidates.append(user_doc.get("email"))

    for candidate in candidates:
        if not candidate:
            continue
        employee = get_employee_by_mobile(candidate) or get_employee_by_email(candidate)
        if employee:
            return employee
    return None


def fetch_user_role(identifier: Any, preferred_role: Any = None) -> Dict[str, Any]:
    normalized_identifier = _normalize_identifier(identifier)
    role = _normalize_role(preferred_role)
    if not normalized_identifier:
        return {"found": False, "role": None, "document": None, "collection": None, "identifier": None}

    search_order = []
    if role == "admin":
        search_order = ["admin", "officer", "user"]
    elif role == "officer":
        search_order = ["officer", "user", "admin"]
    elif role == "user":
        search_order = ["user", "officer", "admin"]
    else:
        search_order = ["admin", "officer", "user"]

    for candidate_role in search_order:
        if candidate_role == "admin":
            doc = get_admin_by_email(normalized_identifier)
            if doc:
                return {
                    "found": True,
                    "role": "admin",
                    "document": doc,
                    "collection": "admins",
                    "identifier": normalized_identifier,
                }

        elif candidate_role == "officer":
            doc = get_officer_by_identifier(normalized_identifier)
            collection_name = "government_officers" if doc else None
            if not doc:
                doc = users_collection.find_one({**_identifier_queries(normalized_identifier), "role": "officer"})
                collection_name = "users" if doc else None
            if doc:
                return {
                    "found": True,
                    "role": "officer",
                    "document": doc,
                    "collection": collection_name or "government_officers",
                    "identifier": normalized_identifier,
                }

        elif candidate_role == "user":
            doc = get_user_by_mobile(normalized_identifier)
            if not doc and "@" in normalized_identifier:
                doc = users_collection.find_one({"email": normalized_identifier})
            if doc:
                return {
                    "found": True,
                    "role": str(doc.get("role", "user")).lower() or "user",
                    "document": doc,
                    "collection": "users",
                    "identifier": normalized_identifier,
                }

            employee = _resolve_employee_record(normalized_identifier)
            if employee:
                return {
                    "found": True,
                    "role": "user",
                    "document": employee,
                    "collection": "govtlist",
                    "identifier": normalized_identifier,
                }

    return {"found": False, "role": None, "document": None, "collection": None, "identifier": normalized_identifier}


def resolve_role(identifier: Any, preferred_role: Any = None) -> Dict[str, Any]:
    return fetch_user_role(identifier, preferred_role=preferred_role)


def authenticate_admin(email: Any, password: Any) -> Dict[str, Any]:
    identifier = _normalize_identifier(email)
    if not identifier:
        return {"ok": False, "reason": "Email is required."}

    admin_doc = get_admin_by_email(identifier)
    if not admin_doc:
        return {"ok": False, "reason": "Admin account not found."}

    status_check = verify_account_status(admin_doc)
    if not status_check["active"]:
        return {"ok": False, "reason": status_check["reason"], "document": admin_doc}

    if not _check_password(password, admin_doc.get("password")):
        return {"ok": False, "reason": "Invalid admin credentials.", "document": admin_doc}

    return {"ok": True, "role": "admin", "document": admin_doc}


def authenticate_officer(identifier: Any, password: Any) -> Dict[str, Any]:
    resolved = fetch_user_role(identifier, preferred_role="officer")
    officer_doc = resolved.get("document")
    if not resolved.get("found") or not officer_doc:
        return {"ok": False, "reason": "Officer account not found."}

    if resolved.get("role") != "officer":
        return {"ok": False, "reason": "Officer account not found."}

    status_check = verify_account_status(officer_doc)
    if not status_check["active"]:
        return {"ok": False, "reason": status_check["reason"], "document": officer_doc}

    stored_password = officer_doc.get("password")
    if stored_password is None:
        legacy_doc = users_collection.find_one({**_identifier_queries(_normalize_identifier(identifier)), "role": "officer"})
        if legacy_doc:
            officer_doc = legacy_doc
            stored_password = legacy_doc.get("password")

    if not _check_password(password, stored_password):
        return {"ok": False, "reason": "Invalid officer credentials.", "document": officer_doc}

    return {"ok": True, "role": "officer", "document": officer_doc}


def authenticate_user(identifier: Any, password: Any = None, via_otp: bool = False) -> Dict[str, Any]:
    normalized_identifier = _normalize_identifier(identifier)
    if not normalized_identifier:
        return {"ok": False, "reason": "Mobile number or email is required."}

    user_doc = get_user_by_mobile(normalized_identifier)
    if not user_doc and "@" in normalized_identifier:
        user_doc = users_collection.find_one({"email": normalized_identifier})

    if not user_doc:
        employee_doc = _resolve_employee_record(normalized_identifier)
        if employee_doc:
            if not via_otp:
                return {
                    "ok": False,
                    "reason": "Identity verification required.",
                    "document": employee_doc,
                    "requires_identity_verification": True,
                }
            return {
                "ok": True,
                "role": "user",
                "document": employee_doc,
                "collection": "govtlist",
                "requires_identity_verification": False,
            }
        return {"ok": False, "reason": "User account not found."}

    status_check = verify_account_status(user_doc)
    if not status_check["active"]:
        return {"ok": False, "reason": status_check["reason"], "document": user_doc}

    user_role = str(user_doc.get("role", "user")).lower()
    if user_role not in {"user", "beneficiary"} and not user_doc.get("is_government_employee"):
        return {"ok": False, "reason": "Please use the dedicated officer or admin login.", "document": user_doc}

    if not via_otp and not user_doc.get("password"):
        return {
            "ok": False,
            "reason": "Use OTP login or reset your password.",
            "document": user_doc,
        }

    if not via_otp:
        if not _check_password(password, user_doc.get("password")):
            return {"ok": False, "reason": "Invalid credentials.", "document": user_doc}

    employee_doc = _resolve_employee_record(normalized_identifier, user_doc)
    if user_doc.get("is_government_employee") or employee_doc:
        employee_status = verify_account_status(employee_doc or user_doc)
        if employee_doc and not employee_status["active"]:
            return {"ok": False, "reason": employee_status["reason"], "document": employee_doc}

        return {
            "ok": True,
            "role": str(user_doc.get("role", "user")).lower() or "user",
            "document": user_doc,
            "employee": employee_doc,
            "collection": "users",
            "requires_identity_verification": False,
        }

    return {
        "ok": False,
        "reason": "Identity verification required.",
        "document": user_doc,
        "requires_identity_verification": True,
    }


def revalidate_session_account(identifier: Any, role: Any) -> Dict[str, Any]:
    resolved = fetch_user_role(identifier, preferred_role=role)
    if not resolved.get("found"):
        return {"ok": False, "reason": "Account not found."}

    account_doc = resolved.get("document")
    status_check = verify_account_status(account_doc)
    if not status_check["active"]:
        return {"ok": False, "reason": status_check["reason"], "document": account_doc}

    return {
        "ok": True,
        "role": resolved.get("role"),
        "document": account_doc,
        "collection": resolved.get("collection"),
    }
