from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from flask import g, has_request_context, request

from database.mongo_client import MONGO_AVAILABLE, audit_logs_collection, claim_logs_collection


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_path = request.path if has_request_context() else "-"
        record.request_method = request.method if has_request_context() else "-"
        record.request_id = getattr(g, "request_id", request.headers.get("X-Request-ID", "-")) if has_request_context() else "-"
        return True


def _build_logger() -> logging.Logger:
    app_logger = logging.getLogger("medicurance")
    if app_logger.handlers:
        return app_logger

    app_logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(levelname)s] %(asctime)s %(request_method)s %(request_path)s %(request_id)s - %(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(RequestContextFilter())
    app_logger.addHandler(handler)
    app_logger.propagate = False
    return app_logger


logger = _build_logger()


def log_audit(actor: str, action: str, description: str, metadata: dict[str, Any] | None = None) -> None:
    try:
        request_ip = request.headers.get("X-Forwarded-For", request.remote_addr) if has_request_context() else None
        log_entry = {
            "actor": actor,
            "action": action,
            "description": description,
            "metadata": metadata or {},
            "ip": request_ip,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if MONGO_AVAILABLE:
            audit_logs_collection.insert_one(log_entry)
        logger.info("[AUDIT] %s | %s | %s", actor, action, description)
    except Exception as exc:
        logger.error("Failed to log audit event: %s", exc)


def log_claim_state(claim_id: str, old_state: str, new_state: str, actor: str, reason: str | None = None) -> None:
    try:
        log_entry = {
            "claim_id": claim_id,
            "transition": f"{old_state} -> {new_state}",
            "old_state": old_state,
            "new_state": new_state,
            "actor": actor,
            "reason": reason or "State transition",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if MONGO_AVAILABLE:
            claim_logs_collection.insert_one(log_entry)
        logger.info("[CLAIM_STATE] %s | %s -> %s by %s", claim_id, old_state, new_state, actor)
    except Exception as exc:
        logger.error("Failed to log claim state transition: %s", exc)
