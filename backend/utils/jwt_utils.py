from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import jwt
from flask import current_app, request

from config.settings import Config
from database.mongo_client import token_revocations_collection
from utils.logger import log_audit


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _settings() -> dict[str, Any]:
    return {
        "secret": Config.JWT_SECRET_KEY or Config.SECRET_KEY,
        "issuer": Config.JWT_ISSUER,
        "access_minutes": int(Config.JWT_ACCESS_EXP_MINUTES or 15),
        "refresh_days": int(Config.JWT_REFRESH_EXP_DAYS or 14),
        "cookie_secure": bool(Config.JWT_COOKIE_SECURE or Config.FLASK_ENV != "development"),
        "cookie_samesite": Config.JWT_COOKIE_SAMESITE or "Lax",
        "cookie_name": Config.JWT_COOKIE_NAME,
        "refresh_cookie_name": Config.JWT_REFRESH_COOKIE_NAME,
    }


def _hash_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def _token_payload(identity: str, role: str, token_type: str, expires_delta: timedelta, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    now = _utcnow()
    jti = secrets.token_urlsafe(24)
    payload: dict[str, Any] = {
        "iss": _settings()["issuer"],
        "sub": str(identity),
        "role": str(role),
        "type": token_type,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": jti,
    }
    if extra:
        payload.update(extra)
    return payload


def create_access_token(identity: str, role: str, extra: Mapping[str, Any] | None = None) -> str:
    settings = _settings()
    payload = _token_payload(identity, role, "access", timedelta(minutes=settings["access_minutes"]), extra)
    return jwt.encode(payload, settings["secret"], algorithm="HS256")


def create_refresh_token(identity: str, role: str, extra: Mapping[str, Any] | None = None) -> str:
    settings = _settings()
    payload = _token_payload(identity, role, "refresh", timedelta(days=settings["refresh_days"]), extra)
    return jwt.encode(payload, settings["secret"], algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    settings = _settings()
    return jwt.decode(token, settings["secret"], algorithms=["HS256"], issuer=settings["issuer"], options={"require": ["exp", "iat", "sub", "jti"]})


def is_token_revoked(token: str) -> bool:
    try:
        payload = decode_token(token)
    except Exception:
        return True
    record = token_revocations_collection.find_one({"jti_hash": _hash_jti(payload.get("jti", ""))})
    return bool(record)


def revoke_token(token: str, reason: str = "logout") -> None:
    try:
        payload = decode_token(token)
    except Exception:
        return

    token_revocations_collection.update_one(
        {"jti_hash": _hash_jti(payload.get("jti", ""))},
        {
            "$set": {
                "jti_hash": _hash_jti(payload.get("jti", "")),
                "token_type": payload.get("type", "unknown"),
                "sub": payload.get("sub"),
                "role": payload.get("role"),
                "reason": reason,
                "revoked_at": _utcnow().isoformat(),
                "expires_at": datetime.fromtimestamp(int(payload.get("exp", _utcnow().timestamp())), tz=timezone.utc).isoformat(),
            }
        },
        upsert=True,
    )


def revoke_refresh_token(token: str, reason: str = "rotation") -> None:
    revoke_token(token, reason=reason)


def extract_bearer_token() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def get_token_from_request(cookie_name: str | None = None) -> str | None:
    cookie_name = cookie_name or _settings()["cookie_name"]
    return request.cookies.get(cookie_name) or extract_bearer_token()


def issue_auth_tokens(identity: str, role: str, extra: Mapping[str, Any] | None = None) -> dict[str, str]:
    access_token = create_access_token(identity, role, extra=extra)
    refresh_token = create_refresh_token(identity, role, extra=extra)
    return {"access_token": access_token, "refresh_token": refresh_token}


def set_auth_cookies(response, tokens: Mapping[str, str]):
    settings = _settings()
    response.set_cookie(
        settings["cookie_name"],
        tokens["access_token"],
        httponly=True,
        secure=settings["cookie_secure"],
        samesite=settings["cookie_samesite"],
        max_age=settings["access_minutes"] * 60,
    )
    response.set_cookie(
        settings["refresh_cookie_name"],
        tokens["refresh_token"],
        httponly=True,
        secure=settings["cookie_secure"],
        samesite=settings["cookie_samesite"],
        max_age=settings["refresh_days"] * 86400,
    )
    return response


def clear_auth_cookies(response):
    settings = _settings()
    response.delete_cookie(settings["cookie_name"])
    response.delete_cookie(settings["refresh_cookie_name"])
    return response


def log_token_event(actor: str, action: str, metadata: Mapping[str, Any] | None = None) -> None:
    log_audit(actor, action, f"JWT token event: {action}", dict(metadata or {}))

