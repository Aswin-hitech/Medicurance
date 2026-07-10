from __future__ import annotations

from functools import wraps
from typing import Callable, Iterable

from flask import flash, redirect, session, url_for

from core.rbac import Role
from services.auth_service import revalidate_session_account
from utils.logger import log_audit


def _normalize_role(role: object) -> str:
    value = str(role or "").strip().lower()
    if value in {Role.CITIZEN.value, "beneficiary", "member"}:
        return Role.CITIZEN.value
    if value == Role.OFFICER.value:
        return Role.OFFICER.value
    if value == Role.ADMIN.value:
        return Role.ADMIN.value
    return Role.CITIZEN.value


def _dashboard_for_role(role: object):
    role_name = _normalize_role(role)
    if role_name == Role.OFFICER.value:
        return url_for("officer.dashboard")
    if role_name == Role.ADMIN.value:
        return url_for("admin.dashboard")
    return url_for("user.dashboard")


def _as_role_set(allowed_roles: object) -> set[str]:
    if isinstance(allowed_roles, str):
        return {_normalize_role(allowed_roles)}
    if isinstance(allowed_roles, Iterable):
        return {_normalize_role(role) for role in allowed_roles}
    return {_normalize_role(allowed_roles)}


def role_required(allowed_roles):
    """
    Decorator to restrict access to specific roles.
    allowed_roles can be a string or a list of strings.
    """
    allowed_role_set = _as_role_set(allowed_roles)

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            identifier = session.get("mobile") or session.get("user_id")
            if not session.get("authenticated") or not identifier:
                flash("Please login to access this page.", "danger")
                return redirect(url_for("auth.login"))

            user_role = _normalize_role(session.get("role", Role.CITIZEN.value))
            validation = revalidate_session_account(identifier, user_role)
            if not validation.get("ok"):
                session.clear()
                flash(validation.get("reason", "Your session is no longer valid. Please log in again."), "danger")
                return redirect(url_for("auth.login"))

            user_role = _normalize_role(validation.get("role", user_role))
            session["authenticated"] = True
            session["role"] = user_role

            if user_role not in allowed_role_set:
                log_audit(identifier, "security_violation", "Unauthorized role access blocked", {"role": user_role, "allowed": allowed_roles})
                flash("Unauthorized access. You do not have permission for this action.", "danger")
                return redirect(_dashboard_for_role(user_role))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator
