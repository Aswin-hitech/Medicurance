from __future__ import annotations

import bcrypt
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from database.admin_repository import get_admin_by_email
from database.government_officer_repository import get_officer_by_identifier
from database.govt_repository import get_employee_by_email, get_employee_by_mobile, get_employee_by_ppo
from database.mongo_client import admins_collection, officers_collection, users_collection
from database.user_repository import get_user_by_email, get_user_by_mobile


ACTIVE_STATUSES = {"active", "enabled", "verified", "approved", "linked"}
INACTIVE_STATUSES = {"inactive", "disabled", "deleted", "removed", "deactivated", "suspended"}


def _normalize_identifier(identifier: Any) -> str:
    """Normalise a login identifier.

    Rules:
    - Email addresses  → lowercase
    - Pure digit strings (phone numbers) → digits only (strip spaces/dashes)
    - Alphanumeric IDs (e.g. "GOVOFF009", "OFF009") → kept as-is
      so MongoDB exact-match on employee_id / officer_id still works.
    """
    value = str(identifier or "").strip()
    if not value:
        return ""
    if "@" in value:
        return value.lower()
    # Only strip to digits when the value IS a phone number
    # (i.e. every character is already a digit or a common separator).
    digits_only = "".join(ch for ch in value if ch.isdigit())
    non_digit = "".join(ch for ch in value if not ch.isdigit())
    if not non_digit:          # pure digit string → phone
        return digits_only
    # Mixed alphanumeric → employee/officer ID, preserve original
    return value


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


def _account_password_hash(account_doc: Optional[Dict[str, Any]]) -> Any:
    if not account_doc:
        return None
    auth = account_doc.get("auth") or {}
    return auth.get("passwordHash") or auth.get("password_hash") or account_doc.get("passwordHash") or account_doc.get("password")


def _check_password(password: Any, stored_password: Any) -> bool:
    if stored_password in (None, ""):
        return False
    hashed = stored_password if isinstance(stored_password, bytes) else str(stored_password).encode("utf-8")
    try:
        return bcrypt.checkpw(_normalize_password(password), hashed)
    except Exception:
        return False


def _auth_query(identifier: str) -> Dict[str, Any]:
    normalized = _normalize_identifier(identifier)
    if not normalized:
        return {}
    if "@" in normalized:
        return {"auth.email": normalized}
    digit_values = _digit_query_values(normalized)
    return {
        "$or": [
            {"auth.phone": normalized},
            {"auth.phone_number": normalized},
            {"auth.phone": {"$in": digit_values}},
            {"auth.phone_number": {"$in": digit_values}},
            {"mobile": normalized},
            {"mobile": {"$in": digit_values}},
            {"phone": normalized},
            {"phone": {"$in": digit_values}},
            {"phone_number": normalized},
            {"phone_number": {"$in": digit_values}},
            {"email": normalized},
            {"auth.email": normalized},
            {"ppoNumber": normalized},
            {"ppo_number": normalized},
            {"auth.ppoNumber": normalized},
            {"auth.ppo_number": normalized},
            {"employee_id": normalized},
            {"officer_id": normalized},
        ]
    }


def _is_deleted(document: Dict[str, Any]) -> bool:
    return bool(document.get("deleted") or document.get("is_deleted") or document.get("deleted_at") or document.get("removed_at"))


def _status_value(document: Dict[str, Any]) -> str:
    # Check flat status fields first
    for key in ("status", "account_status", "state"):
        value = document.get(key)
        if value:
            return str(value).strip().lower()
    # govtlist uses auth.accountStatus
    auth_status = (document.get("auth") or {}).get("accountStatus")
    if auth_status:
        return str(auth_status).strip().lower()
    # govtofficers uses employment.status
    emp_status = (document.get("employment") or {}).get("status")
    if emp_status:
        return str(emp_status).strip().lower()
    if document.get("is_disabled"):
        return "disabled"
    if document.get("active") is False:
        return "inactive"
    return "active"


def verify_account_status(account_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not account_doc:
        return {"active": False, "reason": "Account not found.", "status": "missing"}

    status = _status_value(account_doc)
    locked_until = account_doc.get("account_locked_until")
    if locked_until:
        try:
            lock_dt = datetime.fromisoformat(str(locked_until).replace("Z", "+00:00"))
            if lock_dt > datetime.now(timezone.utc):
                return {"active": False, "reason": "Account is temporarily locked.", "status": "locked"}
        except Exception:
            pass
    if _is_deleted(account_doc):
        return {"active": False, "reason": "Account has been deleted.", "status": status}
    if status in INACTIVE_STATUSES:
        return {"active": False, "reason": "Account is disabled.", "status": status}
    if status and status not in ACTIVE_STATUSES:
        return {"active": False, "reason": "Account is not active.", "status": status}

    if str(account_doc.get("role", "")).strip().lower() == "officer" or account_doc.get("officer_id") or account_doc.get("employee_id"):
        has_officer_id = bool(account_doc.get("officer_id") or account_doc.get("employee_id"))
        # Properly access nested auth.phone — dict.get("auth.phone") never works
        auth_block = account_doc.get("auth") or {}
        has_phone = bool(
            auth_block.get("phone")
            or auth_block.get("phone_number")
            or account_doc.get("phone")
            or account_doc.get("phone_number")
            or account_doc.get("mobile")
        )
        if not has_officer_id and not has_phone:
            return {"active": False, "reason": "Officer record is incomplete.", "status": status}

    if str(account_doc.get("role", "")).strip().lower() == "admin" and not (account_doc.get("email") or (account_doc.get("auth") or {}).get("email")):
        return {"active": False, "reason": "Admin record is incomplete.", "status": status}

    return {"active": True, "reason": "Account is active.", "status": status or "active"}


def needs_verification(user: Optional[Dict[str, Any]]) -> bool:
    """Return True only when a user/officer record still needs identity verification.

    Govtofficers documents do NOT have a top-level 'role' field; the role lives
    inside 'permissions.role'.  Detect officers by the presence of 'officer_id'
    or 'employee_id' at the top level instead.
    """
    if not user:
        return True

    # Detect an officer document by top-level officer_id / employee_id
    is_officer_doc = bool(
        user.get("officer_id")
        or user.get("employee_id")
        or str(user.get("role", "")).strip().lower() == "officer"
    )

    if is_officer_doc and verify_account_status(user).get("active"):
        return False

    if user.get("government_verified") is True or user.get("verification_completed") is True or user.get("is_verified") is True:
        return False

    return True


def _resolve_beneficiary_record(identifier: str, user_doc: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    candidates = [identifier]
    if user_doc:
        auth = user_doc.get("auth") or {}
        for candidate in (
            auth.get("phone"),
            auth.get("phone_number"),
            auth.get("email"),
            auth.get("ppoNumber"),
            auth.get("ppo_number"),
            user_doc.get("mobile"),
            user_doc.get("phone"),
            user_doc.get("phone_number"),
            user_doc.get("email"),
            user_doc.get("ppoNumber"),
            user_doc.get("ppo_number"),
        ):
            if candidate:
                candidates.append(candidate)

    for candidate in candidates:
        if not candidate:
            continue
        employee = get_employee_by_mobile(candidate) or get_employee_by_email(candidate) or get_employee_by_ppo(candidate)
        if employee:
            return employee
    return None


def _resolve_officer_record(identifier: str) -> Optional[Dict[str, Any]]:
    return get_officer_by_identifier(identifier)


def fetch_user_role(identifier: Any, preferred_role: Any = None) -> Dict[str, Any]:
    normalized_identifier = _normalize_identifier(identifier)
    role = _normalize_role(preferred_role)
    if not normalized_identifier:
        return {"found": False, "role": None, "document": None, "collection": None, "identifier": None}

    search_order = ["admin", "officer", "user"]
    if role == "officer":
        search_order = ["officer", "user", "admin"]
    elif role == "user":
        # Beneficiaries live in govtlist — check there first before users collection
        search_order = ["govtlist", "users", "officer", "admin"]

    for candidate_role in search_order:
        if candidate_role == "admin":
            doc = get_admin_by_email(normalized_identifier) if "@" in normalized_identifier else None
            if doc:
                return {"found": True, "role": "admin", "document": doc, "collection": "admins", "identifier": normalized_identifier}

        elif candidate_role == "officer":
            doc = _resolve_officer_record(normalized_identifier)
            if doc:
                return {"found": True, "role": "officer", "document": doc, "collection": "govtofficers", "identifier": normalized_identifier}

        elif candidate_role == "govtlist":
            # Check govtlist (beneficiary collection) by auth.email or auth.phone
            employee = _resolve_beneficiary_record(normalized_identifier)
            if employee:
                return {"found": True, "role": "user", "document": employee, "collection": "govtlist", "identifier": normalized_identifier}

        elif candidate_role == "users":
            doc = get_user_by_mobile(normalized_identifier) or get_user_by_email(normalized_identifier)
            if doc:
                return {"found": True, "role": str(doc.get("role", "user")).lower() or "user", "document": doc, "collection": "users", "identifier": normalized_identifier}

        elif candidate_role == "user":
            doc = get_user_by_mobile(normalized_identifier) or get_user_by_email(normalized_identifier)
            if doc:
                return {"found": True, "role": str(doc.get("role", "user")).lower() or "user", "document": doc, "collection": "users", "identifier": normalized_identifier}
            employee = _resolve_beneficiary_record(normalized_identifier)
            if employee:
                return {"found": True, "role": "user", "document": employee, "collection": "govtlist", "identifier": normalized_identifier}

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
    stored_password = admin_doc.get("password") or _account_password_hash(admin_doc)
    if not _check_password(password, stored_password):
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

    # govtofficers schema stores the hash at auth.passwordHash (nested).
    # Fall back to flat 'password' for legacy/migrated records.
    stored_password = (
        (officer_doc.get("auth") or {}).get("passwordHash")
        or officer_doc.get("password")
    )
    if stored_password is None:
        # Check legacy users collection
        legacy_doc = users_collection.find_one({**_auth_query(_normalize_identifier(identifier)), "role": "officer"})
        if legacy_doc:
            officer_doc = legacy_doc
            stored_password = (
                (legacy_doc.get("auth") or {}).get("passwordHash")
                or legacy_doc.get("password")
            )

    if not _check_password(password, stored_password):
        return {"ok": False, "reason": "Invalid officer credentials.", "document": officer_doc}

    return {"ok": True, "role": "officer", "document": officer_doc}


def authenticate_user(identifier: Any, password: Any = None, via_otp: bool = False) -> Dict[str, Any]:
    """Authenticate a beneficiary (govtlist) or legacy users-collection account.

    Password login:  verifies auth.passwordHash in govtlist.
    OTP login:       skips password check entirely.
    """
    normalized_identifier = _normalize_identifier(identifier)
    if not normalized_identifier:
        return {"ok": False, "reason": "Mobile number or email is required."}

    # ── 1. Primary lookup: govtlist (beneficiary collection) ─────────────────
    employee_doc = _resolve_beneficiary_record(normalized_identifier)
    if employee_doc:
        auth_block = employee_doc.get("auth") or {}
        account_status = str(auth_block.get("accountStatus") or "Active").lower()
        if account_status in INACTIVE_STATUSES:
            return {"ok": False, "reason": "Account is disabled.", "document": employee_doc}

        if not via_otp:
            stored_password = auth_block.get("passwordHash")
            if not stored_password:
                return {"ok": False, "reason": "No password set. Use OTP login or contact admin.", "document": employee_doc}
            if not _check_password(password, stored_password):
                return {"ok": False, "reason": "Invalid credentials.", "document": employee_doc}

        return {
            "ok": True,
            "role": "user",
            "document": employee_doc,
            "collection": "govtlist",
            "requires_identity_verification": False,
        }

    # ── 2. Fallback: legacy users collection ──────────────────────────────────
    user_doc = get_user_by_mobile(normalized_identifier) or get_user_by_email(normalized_identifier)
    if not user_doc:
        return {"ok": False, "reason": "User account not found."}

    status_check = verify_account_status(user_doc)
    if not status_check["active"]:
        return {"ok": False, "reason": status_check["reason"], "document": user_doc}

    if not via_otp:
        stored_password = _account_password_hash(user_doc)
        if not stored_password:
            return {"ok": False, "reason": "Use OTP login or reset your password.", "document": user_doc}
        if not _check_password(password, stored_password):
            return {"ok": False, "reason": "Invalid credentials.", "document": user_doc}

    return {
        "ok": True,
        "role": str(user_doc.get("role", "user")).lower() or "user",
        "document": user_doc,
        "employee": None,
        "collection": "users",
        "requires_identity_verification": False,
    }


def revalidate_session_account(identifier: Any, role: Any) -> Dict[str, Any]:
    resolved = fetch_user_role(identifier, preferred_role=role)
    if not resolved.get("found"):
        return {"ok": False, "reason": "Account not found."}
    account_doc = resolved.get("document")
    status_check = verify_account_status(account_doc)
    if not status_check["active"]:
        return {"ok": False, "reason": status_check["reason"], "document": account_doc}
    return {"ok": True, "role": resolved.get("role"), "document": account_doc, "collection": resolved.get("collection")}
