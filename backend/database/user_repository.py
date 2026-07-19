from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

class UserRepository:
    def __init__(self, db):
        self.db = db

    def _normalize_mobile(self, value: object) -> str:
        return "".join(ch for ch in str(value or "").strip() if ch.isdigit())

    def _normalize_email(self, value: object) -> str:
        return str(value or "").strip().lower()

    def get_user_by_mobile(self, mobile: object):
        normalized = self._normalize_mobile(mobile)
        if not normalized:
            return None
        return self.db["users"].find_one({
            "$or": [
                {"mobile": normalized},
                {"phone": normalized},
                {"auth.phone": normalized},
                {"auth.phone_number": normalized},
            ]
        })

    def get_user_by_email(self, email: object):
        normalized = self._normalize_email(email)
        if not normalized:
            return None
        return self.db["users"].find_one({
            "$or": [
                {"email": normalized},
                {"auth.email": normalized},
            ]
        })

    def create_user(self, mobile: object, password: object, role: str = "user", extra_fields: Optional[Dict[str, Any]] = None):
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "mobile": self._normalize_mobile(mobile),
            "auth": {
                "phone": self._normalize_mobile(mobile),
                "passwordHash": password,
            },
            "role": role,
            "status": "Active",
            "account_status": "Active",
            "is_disabled": False,
            "is_deleted": False,
            "created_at": now,
            "updated_at": now,
            "is_government_employee": False,
        }
        payload.update(extra_fields or {})
        email = payload.pop("email", None)
        if email:
            payload.setdefault("auth", {})
            payload["auth"]["email"] = self._normalize_email(email)
        ppo_number = payload.pop("ppo_number", None)
        if ppo_number:
            payload.setdefault("auth", {})
            payload["auth"]["ppoNumber"] = str(ppo_number).strip()
        self.db["users"].update_one(
            {"mobile": payload["mobile"]},
            {"$set": payload},
            upsert=True,
        )

    def update_password(self, mobile: object, new_password: object):
        self.db["users"].update_one(
            {"$or": [
                {"mobile": self._normalize_mobile(mobile)},
                {"phone": self._normalize_mobile(mobile)},
                {"auth.phone": self._normalize_mobile(mobile)},
            ]},
            {"$set": {
                "auth.passwordHash": new_password,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )


_default_repo: UserRepository | None = None


def _repo() -> UserRepository:
    global _default_repo
    if _default_repo is None:
        from database.mongo_client import db

        _default_repo = UserRepository(db)
    return _default_repo


def get_user_by_mobile(mobile: object):
    return _repo().get_user_by_mobile(mobile)


def get_user_by_email(email: object):
    return _repo().get_user_by_email(email)


def create_user(mobile: object, password: object, role: str = "user", extra_fields: Optional[Dict[str, Any]] = None):
    return _repo().create_user(mobile, password, role=role, extra_fields=extra_fields)


def update_password(mobile: object, new_password: object):
    return _repo().update_password(mobile, new_password)
