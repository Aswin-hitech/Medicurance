def verify_hospital(hospital_name):
    from database.hospital_repository import get_hospital_by_name, get_hospital_by_identifier

    hospital = get_hospital_by_identifier(hospital_name) or get_hospital_by_name(hospital_name)

    if hospital:
        return {
            "verified": True,
            "network": hospital.get("cashless", hospital.get("network", False))
        }

    return {
        "verified": False,
        "network": False
    }
