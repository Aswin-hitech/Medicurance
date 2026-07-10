from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from database.mongo_client import hospitals_collection
from utils.cache import cache_manager, ttl_cache
from config.settings import Config


def _normalize_hospital_name(name):
    return str(name or "").strip().lower()


def _case_insensitive_match(name):
    normalized = _normalize_hospital_name(name)
    return {"$regex": f"^{normalized}$", "$options": "i"}


def add_hospital(name, city, state, network=True):
    normalized_name = _normalize_hospital_name(name)
    existing = hospitals_collection.find_one({"name": _case_insensitive_match(normalized_name)})
    if existing:
        return None

    data = {
        "name": normalized_name,
        "city": city,
        "state": state,
        "network": network,
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    cache_manager.delete(f"hospital:{normalized_name}")
    return hospitals_collection.insert_one(data)


@ttl_cache("hospital", ttl_seconds=getattr(Config, "HOSPITAL_CACHE_TTL_SECONDS", 900), key_builder=lambda name: _normalize_hospital_name(name))
def get_hospital_by_name(name):
    return hospitals_collection.find_one({"name": _case_insensitive_match(name), "is_deleted": {"$ne": True}})


def get_all_hospitals():
    return list(hospitals_collection.find({"is_deleted": {"$ne": True}}).sort("name", 1))


def remove_hospital(name):
    normalized = _normalize_hospital_name(name)
    cache_manager.delete(f"hospital:{normalized}")
    return hospitals_collection.update_one(
        {"name": _case_insensitive_match(name)},
        {"$set": {"is_deleted": True, "deleted_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}},
    )


def update_hospital_network(name, status):
    cache_manager.delete(f"hospital:{_normalize_hospital_name(name)}")
    return hospitals_collection.update_one(
        {"name": _case_insensitive_match(name)},
        {"$set": {"network": status, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )


def verify_hospital(name):
    hospital = get_hospital_by_name(name)
    if hospital:
        return {
            "exists": True,
            "verified": True,
            "network": hospital.get("network", False)
        }
    return {
        "exists": False,
        "verified": False,
        "network": False
    }
