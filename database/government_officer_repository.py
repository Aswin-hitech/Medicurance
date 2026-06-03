import re
from datetime import datetime, timezone

from database.mongo_client import officers_collection


def _normalize_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _digit_query_values(value):
    normalized = _normalize_digits(value)
    if not normalized:
        return []
    values = [normalized]
    try:
        values.append(int(normalized))
    except ValueError:
        pass
    return values


def _normalize_identifier(value):
    raw = str(value or "").strip().lower()
    if "@" in raw:
        return raw
    digits = _normalize_digits(raw)
    return digits or raw


def _normalize_officer_payload(officer_data):
    payload = dict(officer_data or {})
    payload.pop('_id', None)

    if payload.get("officer_id") is not None:
        payload["officer_id"] = str(payload.get("officer_id")).strip()
    if payload.get("employee_id") is not None:
        payload["employee_id"] = str(payload.get("employee_id")).strip()
    if payload.get("email") is not None:
        payload["email"] = str(payload.get("email")).strip().lower()

    phone_number = payload.get("phone_number") or payload.get("phone") or payload.get("mobile")
    phone_number = _normalize_digits(phone_number)
    if phone_number:
        payload["phone_number"] = phone_number
        payload["phone"] = phone_number
        payload["mobile"] = phone_number

    joining_date = str(payload.get("joining_date") or payload.get("date_of_joining") or "").strip()
    if joining_date:
        payload["joining_date"] = joining_date
        payload["date_of_joining"] = joining_date

    if payload.get("status") in (None, ""):
        payload["status"] = "Active"

    payload.setdefault("is_verified", False)
    payload.setdefault("government_verified", False)
    payload.setdefault("verification_completed", False)
    return payload


def _normalize_officer_update_data(update_data):
    payload = dict(update_data or {})
    payload.pop('_id', None)

    if payload.get("officer_id") is not None:
        payload["officer_id"] = str(payload.get("officer_id")).strip()
    if payload.get("employee_id") is not None:
        payload["employee_id"] = str(payload.get("employee_id")).strip()
    if payload.get("email") is not None:
        payload["email"] = str(payload.get("email")).strip().lower()

    phone_number = payload.get("phone_number") or payload.get("phone") or payload.get("mobile")
    phone_number = _normalize_digits(phone_number)
    if phone_number:
        payload["phone_number"] = phone_number
        payload["phone"] = phone_number
        payload["mobile"] = phone_number

    joining_date = str(payload.get("joining_date") or payload.get("date_of_joining") or "").strip()
    if joining_date:
        payload["joining_date"] = joining_date
        payload["date_of_joining"] = joining_date

    return payload


def _officer_query(identifier):
    normalized = _normalize_identifier(identifier)
    if not normalized:
        return {}

    if "@" in normalized:
        return {"email": normalized}

    digit_values = _digit_query_values(normalized)
    return {
        "$or": [
            {"officer_id": normalized},
            {"employee_id": normalized},
            {"phone_number": {"$in": digit_values}},
            {"phone": {"$in": digit_values}},
            {"mobile": {"$in": digit_values}},
        ]
    }

def add_officer(officer_data):
    """
    Adds a new government officer.
    """
    payload = _normalize_officer_payload(officer_data)
    identifier = _normalize_identifier(
        payload.get("officer_id")
        or payload.get("employee_id")
        or payload.get("email")
        or payload.get("phone_number")
        or payload.get("phone")
        or payload.get("mobile")
    )
    if not identifier:
        raise ValueError("Officer identifier is required")

    if not payload.get("officer_id"):
        payload["officer_id"] = payload.get("employee_id") or payload.get("phone_number") or identifier
    if not payload.get("employee_id"):
        payload["employee_id"] = payload.get("officer_id")

    query = _officer_query(identifier)
    officers_collection.update_one(query if query else {"officer_id": payload.get("officer_id")}, {"$set": payload}, upsert=True)
    return officers_collection.find_one(query if query else {"officer_id": payload.get("officer_id")})


def create_officer_account(officer_data):
    """
    Create or update a login-enabled officer account in the officers collection.
    """
    payload = _normalize_officer_payload(officer_data)
    payload.setdefault("role", "officer")
    payload.setdefault("is_disabled", False)
    payload.setdefault("is_deleted", False)
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    identifier = _normalize_identifier(
        payload.get("officer_id")
        or payload.get("employee_id")
        or payload.get("email")
        or payload.get("phone_number")
        or payload.get("phone")
        or payload.get("mobile")
    )
    if not identifier:
        raise ValueError("Officer identifier is required")

    if not payload.get("officer_id"):
        payload["officer_id"] = payload.get("employee_id") or payload.get("phone_number") or identifier
    if not payload.get("employee_id"):
        payload["employee_id"] = payload.get("officer_id")

    query = _officer_query(identifier)
    officers_collection.update_one(
        query if query else {"officer_id": payload.get("officer_id")},
        {"$set": payload},
        upsert=True
    )
    return officers_collection.find_one(query if query else {"officer_id": payload.get("officer_id")})

def delete_officer(officer_id):
    """
    Deletes an officer by officer_id.
    """
    if not officer_id:
        return None
    result = officers_collection.delete_one({
        "$or": [
            {"officer_id": str(officer_id).strip()},
            {"employee_id": str(officer_id).strip()},
        ]
    })
    return result.deleted_count

def update_officer(officer_id, update_data):
    """
    Updates an officer by officer_id.
    """
    if not officer_id:
        return None
    update_data = _normalize_officer_update_data(update_data)
    result = officers_collection.update_one(
        {
            "$or": [
                {"officer_id": str(officer_id).strip()},
                {"employee_id": str(officer_id).strip()},
            ]
        },
        {"$set": update_data}
    )
    return result.modified_count

def search_officer(query=None, department=None, district=None):
    """
    Searches officers with search query text and optional filters.
    """
    find_query = {}
    
    if query:
        search_regex = {"$regex": str(query), "$options": "i"}
        digit_values = _digit_query_values(query)
        find_query["$or"] = [
            {"name": search_regex},
            {"officer_id": search_regex},
            {"employee_id": search_regex},
            {"designation": search_regex},
            {"phone": search_regex},
            {"phone_number": search_regex},
            {"email": search_regex}
        ]
        if digit_values:
            find_query["$or"].extend([
                {"phone": {"$in": digit_values}},
                {"phone_number": {"$in": digit_values}},
                {"mobile": {"$in": digit_values}},
            ])
        
    if department:
        find_query["department"] = department
        
    if district:
        find_query["district"] = district
        
    return list(officers_collection.find(find_query))

def get_all_officers():
    """
    Retrieves all government officers.
    """
    return list(officers_collection.find())


def get_officer_by_identifier(identifier):
    """
    Fetch an officer by email, mobile/phone, employee_id, or officer_id.
    """
    normalized = _normalize_identifier(identifier)
    if not normalized:
        return None

    if "@" in normalized:
        return officers_collection.find_one({"email": normalized})

    digit_values = _digit_query_values(normalized)
    return officers_collection.find_one(
        {
            "$or": [
                {"employee_id": normalized},
                {"officer_id": normalized},
                {"phone": {"$in": digit_values}},
                {"mobile": {"$in": digit_values}},
                {"phone_number": {"$in": digit_values}},
            ]
        }
    )
