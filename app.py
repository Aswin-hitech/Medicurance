from importlib.util import find_spec
from pathlib import Path

import socket
import uuid

from flask import Flask, g, request
from config.settings import Config
from flask_wtf.csrf import CSRFProtect
from datetime import timedelta
from utils.rate_limiter import limiter
from utils.logger import logger
from utils.exceptions import register_error_handlers

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
from blueprints.chat_api import chat_api_bp

# Security & Session Settings
app.config['SESSION_COOKIE_SECURE'] = Config.FLASK_ENV != "development"
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
app.config['WTF_CSRF_TIME_LIMIT'] = 3600  # 1 hour limit in seconds
app.config['MAX_CONTENT_LENGTH'] = Config.UPLOAD_MAX_SIZE
app.config["DEBUG"] = Config.FLASK_ENV == "development"
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["PREFERRED_URL_SCHEME"] = "https" if Config.FLASK_ENV != "development" else "http"
app.config["JSON_SORT_KEYS"] = False

csrf = CSRFProtect(app)
if hasattr(limiter, "init_app"):
    limiter.init_app(app)

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(user_bp)
app.register_blueprint(officer_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)
app.register_blueprint(chat_api_bp)

register_error_handlers(app)


@app.before_request
def _assign_request_context():
    g.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())


@app.after_request
def _apply_security_headers(response):
    response.headers["X-Request-ID"] = getattr(g, "request_id", str(uuid.uuid4()))
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Allow the chatbot page to request mic access; the browser still prompts the user.
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https:; "
        "img-src 'self' data: https:; "
        "script-src 'self' https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com 'unsafe-inline'; "
        "style-src 'self' https://fonts.googleapis.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
        "connect-src 'self' https:"
    )

    origin = request.headers.get("Origin")
    allowed_origins = {item.strip() for item in str(Config.CORS_ALLOWED_ORIGINS or "").split(",") if item.strip()}
    if origin and origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Request-ID"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    return response


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
    ]
    annexure_ia_raw = str(getattr(Config, "ANNEXURE_IA_PATH", "") or "").strip()
    annexure_ia_path = Path(annexure_ia_raw) if annexure_ia_raw else None
    if annexure_ia_path and annexure_ia_path.is_file():
        annexure_paths.append(annexure_ia_path)
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
