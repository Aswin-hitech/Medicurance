from functools import wraps
from flask import flash, redirect, session, url_for

from services.auth_service import revalidate_session_account
from utils.logger import log_audit


def _dashboard_for_role(role):
    role = str(role or "").strip().lower()
    if role == "officer":
        return url_for("officer.dashboard")
    if role == "admin":
        return url_for("admin.dashboard")
    return url_for("user.dashboard")

def role_required(allowed_roles):
    """
    Decorator to restrict access to specific roles.
    allowed_roles can be a string or a list of strings.
    """
    if isinstance(allowed_roles, str):
        allowed_roles = [allowed_roles]
        
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            identifier = session.get("mobile") or session.get("user_id")
            if not session.get("authenticated") or not identifier:
                flash("Please login to access this page.", "danger")
                return redirect(url_for("auth.login"))

            user_role = str(session.get("role", "user")).strip().lower()
            validation = revalidate_session_account(identifier, user_role)
            if not validation.get("ok"):
                session.clear()
                flash(validation.get("reason", "Your session is no longer valid. Please log in again."), "danger")
                return redirect(url_for("auth.login"))

            user_role = str(validation.get("role", user_role)).strip().lower()
            session["authenticated"] = True
            session["role"] = user_role

            if user_role not in allowed_roles:
                log_audit(identifier, "security_violation", "Unauthorized role access blocked", {"role": user_role, "allowed": allowed_roles})
                flash("Unauthorized access. You do not have permission for this action.", "danger")
                return redirect(_dashboard_for_role(user_role))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator
