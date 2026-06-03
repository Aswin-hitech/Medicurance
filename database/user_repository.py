from datetime import datetime, timezone

from database.mongo_client import users_collection


def _normalize_mobile(value):
    return "".join(ch for ch in str(value or "").strip() if ch.isdigit())


def get_user_by_mobile(mobile):
    normalized = _normalize_mobile(mobile)
    if not normalized:
        return None
    return users_collection.find_one({"mobile": normalized})


def create_user(mobile, password, role="user", extra_fields=None):
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "mobile": _normalize_mobile(mobile),
        "password": password,
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
    users_collection.update_one(
        {"mobile": payload["mobile"]},
        {"$set": payload},
        upsert=True,
    )


def update_password(mobile, new_password):
    users_collection.update_one(
        {"mobile": _normalize_mobile(mobile)},
        {"$set": {
            "password": new_password,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }}
    )
