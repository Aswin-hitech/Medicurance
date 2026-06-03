"""
Compatibility layer.

The active authentication implementation now lives in `blueprints/auth.py`.
This module keeps legacy imports working without maintaining a second auth flow.
"""

from blueprints.auth import (
    login as login_page,
    send_otp_route as send_otp,
    verify_otp_route as verify_login,
    register_page,
    register_submit as register_user,
    login_submit as login_user,
    forgot_password,
    reset_otp_request as send_reset_otp,
    reset_password_submit as reset_password,
    logout,
)

__all__ = [
    "login_page",
    "send_otp",
    "verify_login",
    "register_page",
    "register_user",
    "login_user",
    "forgot_password",
    "send_reset_otp",
    "reset_password",
    "logout",
]
