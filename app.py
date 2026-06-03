import socket
from importlib.util import find_spec
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, request, session, url_for
from werkzeug.exceptions import HTTPException
from config.settings import Config
from flask_wtf.csrf import CSRFProtect
from datetime import timedelta
from utils.rate_limiter import limiter
from utils.logger import logger

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY

# Fail fast on missing runtime configuration before the app wires routes.
Config.validate()

# Import Blueprints after startup validation
from blueprints.auth import auth_bp
from blueprints.user import user_bp
from blueprints.officer import officer_bp
from blueprints.admin import admin_bp
from blueprints.api import api_bp

# Security & Session Settings
app.config['SESSION_COOKIE_SECURE'] = Config.FLASK_ENV != "development"
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
app.config['MAX_CONTENT_LENGTH'] = Config.UPLOAD_MAX_SIZE
app.config["DEBUG"] = Config.FLASK_ENV == "development"

csrf = CSRFProtect(app)
if hasattr(limiter, "init_app"):
    limiter.init_app(app)

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(user_bp)
app.register_blueprint(officer_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)

# Error handling or global routes can go here
@app.errorhandler(404)
def page_not_found(e):
    return redirect(url_for('auth.login'))


@app.errorhandler(413)
def request_too_large(e):
    flash("The uploaded file is too large. Please choose a smaller file.", "danger")
    return redirect(request.referrer or url_for("user.claim_request"))


@app.errorhandler(Exception)
def unhandled_exception(e):
    if isinstance(e, HTTPException):
        return e

    logger.exception("[ERROR] Unhandled application error: %s", e)
    if request.path.startswith("/api/"):
        return jsonify({"error": "internal_server_error", "message": "Something went wrong."}), 500

    flash("Something went wrong. Please try again.", "danger")
    role = str(session.get("role", "")).strip().lower()
    if role == "admin":
        return redirect(url_for("admin.dashboard"))
    if role == "officer":
        return redirect(url_for("officer.dashboard"))
    if role == "user" and session.get("authenticated"):
        return redirect(url_for("user.dashboard"))
    return redirect(url_for("auth.login"))


def _local_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _startup_health_report():
    report = []

    def add(name, status, details):
        report.append({"name": name, "status": status, "details": details})

    try:
        from database.mongo_client import db

        db.command("ping")
        add("MongoDB", "Healthy", "Ping succeeded.")
    except Exception as exc:
        add("MongoDB", "Degraded", f"Ping failed: {exc}")

    add("Groq", "Configured" if Config.GROQ_API_KEY and Config.GROQ_MODEL else "Missing", f"Model: {Config.GROQ_MODEL or 'not set'}")
    add("OCR.Space", "Configured" if Config.OCR_SPACE_API_KEY else "Missing", "API key present." if Config.OCR_SPACE_API_KEY else "API key missing.")
    add("Supabase", "Configured" if Config.SUPABASE_URL and Config.SUPABASE_SECRET_KEY else "Missing", "Storage credentials present." if Config.SUPABASE_URL and Config.SUPABASE_SECRET_KEY else "Storage credentials missing.")

    vector_path = Path(Config.VECTOR_DB_PATH)
    annexure_paths = [
        Path(Config.ANNEXURE_PATH),
        Path(getattr(Config, "ANNEXURE_IA_PATH", "resources/annexures/annexure_IA.pdf")),
    ]
    try:
        from services.vector_store_manager import validate_vector_store

        vector_status = validate_vector_store()
        missing_annexures = [str(path) for path in annexure_paths if not path.exists()]
        rag_status = "Healthy" if vector_status.get("ok") and not missing_annexures else "Missing"
        details = f"Vector store: {vector_path}; Annexures: {', '.join(str(path) for path in annexure_paths)}; rebuilt={vector_status.get('rebuilt')}"
        if missing_annexures:
            details += f"; missing={', '.join(missing_annexures)}"
        add("RAG Assets", rag_status, details)
    except Exception as exc:
        add("RAG Assets", "Degraded", f"Vector validation failed: {exc}")

    if find_spec("sentence_transformers") is None:
        add("RAG Embeddings", "Missing", "sentence-transformers is not installed.")
    else:
        try:
            from services.rag_service import retrieve_rules

            sample = retrieve_rules("health check", k=1)
            add("RAG Embeddings", "Healthy" if sample is not None else "Degraded", "Vector retrieval returned a response." if sample is not None else "Vector retrieval unavailable.")
        except Exception as exc:
            add("RAG Embeddings", "Degraded", f"Embedding lookup failed: {exc}")

    try:
        from services.storage_service import _get_client

        supabase_client = _get_client()
        add("Supabase Client", "Healthy" if supabase_client is not None else "Missing", "Client initialised." if supabase_client is not None else "Client unavailable.")
    except Exception as exc:
        add("Supabase Client", "Degraded", f"Client init failed: {exc}")

    logger.info("[Startup] MediCurance health report:")
    for item in report:
        logger.info("[Startup] %s | %s | %s", item["name"], item["status"], item["details"])

    return report


if __name__ == "__main__":
    _startup_health_report()
    print(f"[MediCurance] Local access: http://{_local_ip()}:5000")
    app.run(host="0.0.0.0", port=5000, debug=Config.FLASK_ENV == "development")
