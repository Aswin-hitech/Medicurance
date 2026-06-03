"""
utils/logger.py
Phase 8 — Advanced Logging System
"""
import logging
from datetime import datetime, timezone
from flask import request, has_request_context
from database.mongo_client import MONGO_AVAILABLE, audit_logs_collection, claim_logs_collection

# Basic application logger
logger = logging.getLogger("medicurance")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('[%(levelname)s] %(asctime)s - %(message)s'))
    logger.addHandler(ch)

def log_audit(actor: str, action: str, description: str, metadata: dict = None):
    """
    Log system-level actions (logins, config changes, officer creation).
    """
    try:
        request_ip = request.headers.get("X-Forwarded-For", request.remote_addr) if has_request_context() else None
        log_entry = {
            "actor": actor,
            "action": action,
            "description": description,
            "metadata": metadata or {},
            "ip": request_ip,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if MONGO_AVAILABLE:
            audit_logs_collection.insert_one(log_entry)
        logger.info(f"[AUDIT] {actor} | {action} | {description}")
    except Exception as e:
        logger.error(f"Failed to log audit event: {e}")

def log_claim_state(claim_id: str, old_state: str, new_state: str, actor: str, reason: str = None):
    """
    Strictly log claim state machine transitions.
    """
    try:
        log_entry = {
            "claim_id": claim_id,
            "transition": f"{old_state} -> {new_state}",
            "old_state": old_state,
            "new_state": new_state,
            "actor": actor,
            "reason": reason or "State transition",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if MONGO_AVAILABLE:
            claim_logs_collection.insert_one(log_entry)
        logger.info(f"[CLAIM_STATE] {claim_id} | {old_state} -> {new_state} by {actor}")
    except Exception as e:
        logger.error(f"Failed to log claim state transition: {e}")
