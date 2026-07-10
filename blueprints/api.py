from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session

from core.rbac import Role
from database.claim_repository import get_all_claims, get_claims_by_user
from database.hospital_repository import get_all_hospitals
from database.mongo_client import audit_logs_collection, claim_logs_collection, claims_collection, users_collection
from utils.api_responses import api_response
from utils.auth_utils import role_required
from utils.jwt_utils import decode_token, get_token_from_request, is_token_revoked
from utils.logger import logger
from config.settings import Config

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _claim_explanation(claim: dict) -> dict:
    return {
        "claim_id": claim.get("claim_id"),
        "decision": claim.get("final_decision") or claim.get("status"),
        "summary": claim.get("recommendation", {}).get("summary") or claim.get("workflow_metadata", {}).get("decision_reasoning", ""),
        "policy_summary": claim.get("ai_result", {}).get("reasoning") or claim.get("rag_result", {}).get("reasoning", ""),
        "trust_score": claim.get("trust_result", {}).get("score", claim.get("trust_score", 0.0)),
        "fraud_level": claim.get("fraud_result", {}).get("fraud_level", "LOW"),
        "sources": claim.get("source_references", []),
        "reflection": claim.get("reflection_notes", []),
        "next_steps": claim.get("recommendation", {}).get("next_steps", []),
        "missing_documents": claim.get("recommendation", {}).get("missing_documents", []),
    }


def _normalize_pagination():
    page = max(int(request.args.get("page", 1)), 1)
    limit = min(max(int(request.args.get("limit", 25)), 1), 100)
    skip = (page - 1) * limit
    return page, limit, skip


@api_bp.route("/auth/status", methods=["GET"])
def auth_status():
    token = get_token_from_request()
    token_payload = None
    if token and not is_token_revoked(token):
        try:
            token_payload = decode_token(token)
        except Exception:
            token_payload = None

    if session.get("authenticated") and (session.get("mobile") or session.get("user_id")):
        payload = {
            "authenticated": True,
            "role": session.get("role", Role.CITIZEN.value),
            "mobile": session.get("mobile"),
            "user_id": session.get("user_id"),
            "token_present": bool(token_payload),
        }
        return api_response(data=payload, message="Authentication status resolved.", status_code=200)

    if token_payload:
        return api_response(
            data={
                "authenticated": True,
                "role": token_payload.get("role"),
                "mobile": token_payload.get("mobile"),
                "user_id": token_payload.get("user_id"),
                "token_present": True,
            },
            message="Authentication status resolved from token.",
            status_code=200,
        )

    return api_response(data={"authenticated": False}, message="Authentication required.", status_code=401, error="unauthorized")


@api_bp.route("/v1/status", methods=["GET"])
def status():
    return api_response(
        data={
            "service": "Medicurance",
            "version": Config.API_VERSION,
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        message="Service status is healthy.",
        status_code=200,
    )


@api_bp.route("/v1/health", methods=["GET"])
def health():
    status_map = {}
    try:
        from database.mongo_client import db

        db.command("ping")
        status_map["mongodb"] = "healthy"
    except Exception as exc:
        status_map["mongodb"] = f"degraded: {exc}"

    try:
        from services.rag_service import retrieve_rules

        status_map["rag"] = "healthy" if retrieve_rules("health check", k=1) else "degraded"
    except Exception as exc:
        status_map["rag"] = f"degraded: {exc}"

    try:
        from services.storage_service import _get_client

        status_map["storage"] = "healthy" if _get_client() is not None else "degraded"
    except Exception as exc:
        status_map["storage"] = f"degraded: {exc}"

    return api_response(data=status_map, message="Health check complete.", status_code=200)


@api_bp.route("/v1/metrics", methods=["GET"])
@role_required(Role.ADMIN.value)
def metrics():
    return api_response(
        data={
            "users": users_collection.count_documents({}),
            "claims": claims_collection.count_documents({"is_deleted": {"$ne": True}}),
            "audit_events": audit_logs_collection.count_documents({}),
            "claim_events": claim_logs_collection.count_documents({}),
        },
        message="Metrics loaded.",
        status_code=200,
    )


@api_bp.route("/v1/agents/status", methods=["GET"])
@role_required([Role.OFFICER.value, Role.ADMIN.value])
def agents_status():
    claims = list(claims_collection.find({"is_deleted": {"$ne": True}}).sort("updated_at", -1).limit(10))
    trace_count = sum(len(claim.get("agent_trace", [])) for claim in claims)
    return api_response(
        data={
            "recent_claims": len(claims),
            "recent_agent_traces": trace_count,
            "workflow": "LangGraph",
        },
        message="Agent status loaded.",
        status_code=200,
    )


@api_bp.route("/claims", methods=["GET"])
def get_user_claims():
    if "mobile" not in session:
        return api_response(message="Unauthorized.", status_code=401, error="unauthorized")
    page, limit, skip = _normalize_pagination()
    claims = get_all_claims(skip=skip, limit=limit) if session.get("role") in {Role.ADMIN.value, Role.OFFICER.value} else get_claims_by_user(session["mobile"], skip=skip, limit=limit)
    for c in claims:
        c["_id"] = str(c["_id"])
    return api_response(data={"claims": claims, "page": page, "limit": limit}, message="Claims loaded.", status_code=200)


@api_bp.route("/claims/<claim_id>", methods=["GET"])
def get_claim_details(claim_id):
    claim = claims_collection.find_one({"claim_id": claim_id, "is_deleted": {"$ne": True}})
    if not claim:
        return api_response(message="Claim not found.", status_code=404, error="not_found")
    claim["_id"] = str(claim["_id"])
    return api_response(data={"claim": claim}, message="Claim loaded.", status_code=200)


@api_bp.route("/claims/<claim_id>/trace", methods=["GET"])
def get_claim_trace(claim_id):
    claim = claims_collection.find_one({"claim_id": claim_id, "is_deleted": {"$ne": True}})
    if not claim:
        return api_response(message="Claim not found.", status_code=404, error="not_found")
    return api_response(
        data={
            "claim_id": claim_id,
            "agent_trace": claim.get("agent_trace", []),
            "workflow_metadata": claim.get("workflow_metadata", {}),
            "retries": claim.get("retries", {}),
        },
        message="Execution trace loaded.",
        status_code=200,
    )


@api_bp.route("/claims/<claim_id>/explanation", methods=["GET"])
def get_claim_explanation(claim_id):
    claim = claims_collection.find_one({"claim_id": claim_id, "is_deleted": {"$ne": True}})
    if not claim:
        return api_response(message="Claim not found.", status_code=404, error="not_found")
    return api_response(data=_claim_explanation(claim), message="AI explanation loaded.", status_code=200)


@api_bp.route("/officer/pending_claims", methods=["GET"])
@role_required(Role.OFFICER.value)
def get_pending_claims():
    page, limit, skip = _normalize_pagination()
    claims = list(claims_collection.find({"status": "Pending", "is_deleted": {"$ne": True}}).sort("created_at", -1).skip(skip).limit(limit))
    for c in claims:
        c["_id"] = str(c["_id"])
    return api_response(data={"claims": claims, "page": page, "limit": limit}, message="Pending claims loaded.", status_code=200)


@api_bp.route("/admin/stats", methods=["GET"])
@role_required(Role.ADMIN.value)
def get_admin_stats():
    users_count = users_collection.count_documents({})
    claims_count = claims_collection.count_documents({"is_deleted": {"$ne": True}})
    return api_response(
        data={
            "total_users": users_count,
            "total_claims": claims_count,
        },
        message="Admin statistics loaded.",
        status_code=200,
    )


@api_bp.route("/hospitals", methods=["GET"])
def get_hospitals():
    hospitals = get_all_hospitals()
    for h in hospitals:
        h["_id"] = str(h.get("_id", ""))
    return api_response(data={"hospitals": hospitals}, message="Hospitals loaded.", status_code=200)


@api_bp.route("/openapi.json", methods=["GET"])
def openapi_spec():
    if not Config.API_DOCS_ENABLED:
        return api_response(message="API docs disabled.", status_code=404, error="not_found")

    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Medicurance API",
            "version": Config.API_VERSION,
            "description": "Claims, authentication, health, and agent workflow endpoints for Medicurance.",
        },
        "paths": {
            "/api/v1/status": {"get": {"summary": "Service status"}},
            "/api/v1/health": {"get": {"summary": "Health check"}},
            "/api/v1/metrics": {"get": {"summary": "Operational metrics"}},
            "/api/claims/{claim_id}/trace": {"get": {"summary": "Claim execution trace"}},
            "/api/claims/{claim_id}/explanation": {"get": {"summary": "AI explanation"}},
        },
    }
    return jsonify(spec)


@api_bp.route("/docs", methods=["GET"])
def docs():
    if not Config.API_DOCS_ENABLED:
        return api_response(message="API docs disabled.", status_code=404, error="not_found")
    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Medicurance API Docs</title>
      <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
      <style>body { margin: 0; background: #f6f7fb; }</style>
    </head>
    <body>
      <div id="swagger-ui"></div>
      <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
      <script>
        window.ui = SwaggerUIBundle({ url: '/api/openapi.json', dom_id: '#swagger-ui' });
      </script>
    </body>
    </html>
    """
    return html
