from datetime import datetime, timezone

from database.mongo_client import hospitals_collection


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
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return hospitals_collection.insert_one(data)


def get_hospital_by_name(name):
    return hospitals_collection.find_one({"name": _case_insensitive_match(name)})


def get_all_hospitals():
    return list(hospitals_collection.find().sort("name", 1))


def remove_hospital(name):
    return hospitals_collection.delete_one({"name": _case_insensitive_match(name)})


def update_hospital_network(name, status):
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
