from flask import Blueprint, jsonify, request, session
from database.mongo_client import claims_collection, users_collection
from database.claim_repository import get_claims_by_user
from database.hospital_repository import get_all_hospitals
from utils.auth_utils import role_required
import json

api_bp = Blueprint('api', __name__, url_prefix='/api')

@api_bp.route('/auth/status', methods=['GET'])
def auth_status():
    if session.get("authenticated") and (session.get("mobile") or session.get("user_id")):
        payload = {"authenticated": True, "role": session.get("role", "user")}
        if session.get("mobile"):
            payload["mobile"] = session["mobile"]
        if session.get("user_id"):
            payload["user_id"] = session["user_id"]
        return jsonify(payload), 200
    return jsonify({"authenticated": False}), 401

@api_bp.route('/claims', methods=['GET'])
def get_user_claims():
    if "mobile" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    claims = get_claims_by_user(session["mobile"])
    # Clean up ObjectId for JSON serialization
    for c in claims:
        c['_id'] = str(c['_id'])
    return jsonify({"claims": claims}), 200

@api_bp.route('/claims/<claim_id>', methods=['GET'])
def get_claim_details(claim_id):
    claim = claims_collection.find_one({"claim_id": claim_id})
    if not claim:
        return jsonify({"error": "Claim not found"}), 404
    claim['_id'] = str(claim['_id'])
    return jsonify({"claim": claim}), 200

@api_bp.route('/officer/pending_claims', methods=['GET'])
@role_required('officer')
def get_pending_claims():
    claims = list(claims_collection.find({"status": "Pending"}))
    for c in claims:
        c['_id'] = str(c['_id'])
    return jsonify({"claims": claims}), 200

@api_bp.route('/admin/stats', methods=['GET'])
@role_required('admin')
def get_admin_stats():
    users_count = users_collection.count_documents({})
    claims_count = claims_collection.count_documents({})
    return jsonify({
        "total_users": users_count,
        "total_claims": claims_count
    }), 200

@api_bp.route('/hospitals', methods=['GET'])
def get_hospitals():
    hospitals = get_all_hospitals()
    for h in hospitals:
        h['_id'] = str(h.get('_id', ''))
    return jsonify({"hospitals": hospitals}), 200
