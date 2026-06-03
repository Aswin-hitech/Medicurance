from database.mongo_client import hospitals_collection


def _normalize_hospital_name(name):
    return str(name or "").strip().lower()


def verify_hospital(hospital_name):

    hospital = hospitals_collection.find_one(
        {"name": _normalize_hospital_name(hospital_name)}
    )

    if hospital:
        return {
            "verified": True,
            "network": hospital.get("network", False)
        }

    return {
        "verified": False,
        "network": False
    }
