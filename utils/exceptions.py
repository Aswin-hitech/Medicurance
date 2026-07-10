from __future__ import annotations

from typing import Any

from flask import flash, jsonify, redirect, request, session, url_for
from werkzeug.exceptions import HTTPException

from utils.api_responses import api_response
from utils.logger import logger


def redirect_for_role() -> Any:
    role = str(session.get("role", "")).strip().lower()
    if role == "admin":
        return redirect(url_for("admin.dashboard"))
    if role == "officer":
        return redirect(url_for("officer.dashboard"))
    if role == "user" and session.get("authenticated"):
        return redirect(url_for("user.dashboard"))
    return redirect(url_for("auth.login"))


def register_error_handlers(app) -> None:
    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return api_response(error="not_found", message="Resource not found.", status_code=404)
        flash("Page not found.", "warning")
        return redirect(url_for("auth.login"))

    @app.errorhandler(413)
    def request_too_large(_exc):
        if request.path.startswith("/api/"):
            return api_response(error="payload_too_large", message="Uploaded file is too large.", status_code=413)
        flash("The uploaded file is too large. Please choose a smaller file.", "danger")
        return redirect(request.referrer or url_for("user.claim_request"))

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc):
        if request.path.startswith("/api/"):
            return api_response(error=exc.name.lower(), message=exc.description, status_code=exc.code or 500)
        return exc

    @app.errorhandler(Exception)
    def unhandled_exception(exc):
        if isinstance(exc, HTTPException):
            return exc

        logger.exception("[ERROR] Unhandled application error: %s", exc)
        if request.path.startswith("/api/"):
            return api_response(
                error="internal_server_error",
                message="Something went wrong.",
                status_code=500,
            )

        flash("Something went wrong. Please try again.", "danger")
        return redirect_for_role()

