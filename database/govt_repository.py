from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, Optional

from utils.logger import log_audit


def _normalize_text(value):
    return " ".join(str(value or "").strip().lower().split())


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


def _normalize_aadhaar(value):
    return _normalize_digits(value)


def _normalize_date(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


class GovtRepository:
    def __init__(self, db):
        self.db = db

    def create_employee(self, employee_data):
        """
        Creates a new government employee record.
        """
        employee_data.pop('_id', None)

        aadhaar_number = _normalize_aadhaar(employee_data.get("aadhaar_number"))
        if not aadhaar_number:
            raise ValueError("aadhaar_number is required")

        employee_data["aadhaar_number"] = aadhaar_number
        if not employee_data.get("aadhaar_last4"):
            employee_data["aadhaar_last4"] = aadhaar_number[-4:]

        now = datetime.now(timezone.utc).isoformat()
        employee_data.setdefault("created_at", now)
        employee_data.setdefault("updated_at", now)
        employee_data.setdefault("mobile_verified", False)

        result = self.db["govtlist"].insert_one(employee_data)
        return result.inserted_id

    def get_employee_by_mobile(self, phone_number):
        """
        Fetches a government employee by their phone number.
        """
        if not phone_number:
            return None
        query_values = _digit_query_values(phone_number)
        if not query_values:
            return None
        return self.db["govtlist"].find_one({
            "$or": [
                {"mobile": {"$in": query_values}},
                {"phone": {"$in": query_values}},
                {"phone_number": {"$in": query_values}},
            ]
        })

    def get_employee_by_email(self, email):
        """
        Fetches a government employee by their email.
        """
        if not email:
            return None
        return self.db["govtlist"].find_one({"email": str(email).strip()})

    def get_employee_by_employee_id(self, employee_id):
        """
        Fetches a government employee by their employee_id.
        """
        if not employee_id:
            return None
        return self.db["govtlist"].find_one({"employee_id": str(employee_id).strip()})

    def get_employee_by_aadhaar(self, aadhaar_number):
        if not aadhaar_number:
            return None
        aadhaar = _normalize_aadhaar(aadhaar_number)
        if not aadhaar:
            return None
        return self.db["govtlist"].find_one({"aadhaar_number": aadhaar})

    def verify_employee_identity(self, identity_data):
        """
        Verify a government employee by Aadhaar, employee ID, name, DOB, department, and designation.
        Returns (is_verified, employee_doc, message).
        """
        aadhaar_number = _normalize_aadhaar(identity_data.get("aadhaar_number"))
        employee_id = str(identity_data.get("employee_id", "")).strip()
        full_name = _normalize_text(identity_data.get("full_name"))
        date_of_birth = _normalize_date(identity_data.get("date_of_birth"))
        department = _normalize_text(identity_data.get("department"))
        designation = _normalize_text(identity_data.get("designation"))
        mobile = _normalize_digits(identity_data.get("mobile") or identity_data.get("phone_number") or identity_data.get("phone"))
        email = str(identity_data.get("email", "")).strip()

        if not all([aadhaar_number, employee_id, full_name, date_of_birth, department, designation]):
            return False, None, "Missing required identity fields."

        aadhaar_query_values = _digit_query_values(aadhaar_number)
        aadhaar_last4 = aadhaar_number[-4:] if aadhaar_number else ""
        aadhaar_last4_values = _digit_query_values(aadhaar_last4)

        employee = self.db["govtlist"].find_one({
            "employee_id": employee_id,
            "$or": [
                {"aadhaar_number": {"$in": aadhaar_query_values}},
                {"aadhaar_last4": {"$in": aadhaar_last4_values}},
            ]
        })

        if not employee:
            return False, None, "No matching government employee record found."

        if _normalize_text(employee.get("name")) != full_name:
            return False, None, "Name does not match official records."
        if _normalize_date(employee.get("date_of_birth")) != date_of_birth:
            return False, None, "Date of birth does not match official records."
        if _normalize_text(employee.get("department")) != department:
            return False, None, "Department does not match official records."
        if _normalize_text(employee.get("designation")) != designation:
            return False, None, "Designation does not match official records."
        if mobile:
            official_mobiles = {
                _normalize_digits(employee.get("mobile")),
                _normalize_digits(employee.get("phone")),
                _normalize_digits(employee.get("phone_number")),
            }
            if mobile not in official_mobiles:
                return False, None, "Mobile number does not match official records."
        if email and _normalize_text(employee.get("email")) != _normalize_text(email):
            return False, None, "Email does not match official records."

        return True, employee, "Verified"

    def link_new_mobile(self, employee_id, mobile):
        """
        Link a verified government employee record to a new mobile number.
        """
        if not employee_id or not mobile:
            return None

        now = datetime.now(timezone.utc).isoformat()
        normalized_mobile = _normalize_digits(mobile)
        mobile_variants = _digit_query_values(mobile)
        result = self.db["govtlist"].update_one(
            {"employee_id": str(employee_id).strip()},
            {"$set": {
                "phone": normalized_mobile,
                "phone_number": normalized_mobile,
                "mobile": normalized_mobile,
                "mobile_verified": True,
                "last_mobile_update": now,
                "updated_at": now,
                "updated_by": "system"
            }}
        )

        if result.matched_count == 0 or result.modified_count == 0:
            raise Exception("Mobile link verification failed")

        updated = self.db["govtlist"].find_one({
            "$or": [
                {"mobile": {"$in": mobile_variants}},
                {"phone": {"$in": mobile_variants}},
                {"phone_number": {"$in": mobile_variants}},
            ]
        })
        if not updated:
            raise Exception("Mobile link verification failed")

        log_audit(
            normalized_mobile,
            "mobile_link",
            f"Linked mobile {normalized_mobile} to employee {employee_id}"
        )
        return updated

    def search_employee(self, query):
        """
        Searches employees by name, department, designation, employee_id, phone, email, or Aadhaar.
        Can accept a string query or a dictionary query.
        """
        if not query:
            return []
        
        if isinstance(query, dict):
            return list(self.db["govtlist"].find(query))
            
        search_regex = {"$regex": str(query), "$options": "i"}
        or_query = [
            {"name": search_regex},
            {"employee_id": search_regex},
            {"email": search_regex},
            {"department": search_regex},
            {"designation": search_regex},
        ]

        digit_values = _digit_query_values(query)
        if digit_values:
            or_query.extend([
                {"phone": {"$in": digit_values}},
                {"phone_number": {"$in": digit_values}},
                {"mobile": {"$in": digit_values}},
                {"aadhaar_number": {"$in": digit_values}},
                {"aadhaar_last4": {"$in": _digit_query_values(str(query)[-4:])}},
            ])

        or_query = {"$or": or_query}
        return list(self.db["govtlist"].find(or_query))

    def bulk_insert_employees(self, employees):
        """
        Inserts a list of employees in bulk.
        """
        if not employees:
            return None
        return self.db["govtlist"].insert_many(employees)

    def update_employee(self, employee_id, update_data):
        """
        Updates an employee record by employee_id.
        """
        if not employee_id:
            return None
        update_data.pop('_id', None)
        if "aadhaar_number" in update_data:
            update_data["aadhaar_number"] = _normalize_aadhaar(update_data["aadhaar_number"])
            update_data.setdefault("aadhaar_last4", update_data["aadhaar_number"][-4:])
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = self.db["govtlist"].update_one(
            {"employee_id": str(employee_id).strip()},
            {"$set": update_data}
        )
        return result.modified_count

    def delete_employee(self, employee_id):
        """
        Deletes an employee record by employee_id.
        """
        if not employee_id:
            return None
        result = self.db["govtlist"].delete_one({"employee_id": str(employee_id).strip()})
        return result.deleted_count

    def get_all_employees(self):
        """
        Retrieves all government employee records.
        """
        return list(self.db["govtlist"].find())


_default_repo: GovtRepository | None = None


def _repo() -> GovtRepository:
    global _default_repo
    if _default_repo is None:
        from database.mongo_client import db

        _default_repo = GovtRepository(db)
    return _default_repo


def create_employee(employee_data: Dict[str, Any]):
    return _repo().create_employee(employee_data)


def get_employee_by_mobile(phone_number: object):
    return _repo().get_employee_by_mobile(phone_number)


def get_employee_by_email(email: object):
    return _repo().get_employee_by_email(email)


def get_employee_by_employee_id(employee_id: object):
    return _repo().get_employee_by_employee_id(employee_id)


def get_employee_by_aadhaar(aadhaar_number: object):
    return _repo().get_employee_by_aadhaar(aadhaar_number)


def verify_employee_identity(identity_data: Dict[str, Any]):
    return _repo().verify_employee_identity(identity_data)


def link_new_mobile(employee_id: object, mobile: object):
    return _repo().link_new_mobile(employee_id, mobile)


def search_employee(query):
    return _repo().search_employee(query)


def bulk_insert_employees(employees: Iterable[Dict[str, Any]]):
    return _repo().bulk_insert_employees(employees)


def update_employee(employee_id: object, update_data: Dict[str, Any]):
    return _repo().update_employee(employee_id, update_data)


def delete_employee(employee_id: object):
    return _repo().delete_employee(employee_id)


def get_all_employees():
    return _repo().get_all_employees()
