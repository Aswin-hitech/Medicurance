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


def _first_present(document, *paths):
    current = document or {}
    for path in paths:
        node = current
        found = True
        for key in str(path).split("."):
            if not isinstance(node, dict) or key not in node:
                found = False
                break
            node = node.get(key)
        if found and node not in (None, ""):
            return node
    return ""


def _nested_query(*values, field_paths):
    clauses = []
    for value in values:
        if value in (None, ""):
            continue
        for path in field_paths:
            clauses.append({path: value})
    return clauses


class GovtRepository:
    def __init__(self, db):
        self.db = db

    def create_employee(self, employee_data):
        """
        Creates a new government employee record.
        """
        employee_data.pop('_id', None)

        aadhaar_number = _normalize_aadhaar(employee_data.get("aadhaar_number") or _first_present(employee_data, "identity.aadhaarNumber", "identity.aadhaar_number"))
        if not aadhaar_number:
            raise ValueError("aadhaar_number is required")

        employee_data.setdefault("identity", {})
        employee_data["identity"]["aadhaarNumber"] = aadhaar_number
        employee_data["identity"]["aadhaar_number"] = aadhaar_number
        employee_data["identity"]["aadhaarLast4"] = aadhaar_number[-4:]
        employee_data["identity"]["aadhaar_last4"] = aadhaar_number[-4:]
        employee_data["aadhaar_number"] = aadhaar_number
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
                {"auth.phone": {"$in": query_values}},
                {"auth.phone_number": {"$in": query_values}},
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
        normalized = str(email).strip().lower()
        return self.db["govtlist"].find_one({
            "$or": [
                {"auth.email": normalized},
                {"email": normalized},
            ]
        })

    def get_employee_by_ppo(self, ppo_number):
        if not ppo_number:
            return None
        normalized = str(ppo_number).strip()
        return self.db["govtlist"].find_one({
            "$or": [
                {"auth.ppoNumber": normalized},
                {"ppoNumber": normalized},
                {"ppo_number": normalized},
                {"ppo": normalized},
            ]
        })

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
        aadhaar_last4 = aadhaar[-4:]
        return self.db["govtlist"].find_one({
            "$or": [
                {"identity.aadhaarNumber": aadhaar},
                {"identity.aadhaar_number": aadhaar},
                {"identity.aadhaarLast4": aadhaar_last4},
                {"identity.aadhaar_last4": aadhaar_last4},
                {"aadhaar_number": aadhaar},
                {"aadhaar_last4": aadhaar_last4},
            ]
        })

    def verify_employee_identity(self, identity_data):
        """
        Verify a government employee by Aadhaar, PPO number, name, and department.
        Returns (is_verified, employee_doc, message).
        """
        aadhaar_number = _normalize_aadhaar(identity_data.get("aadhaar_number"))
        ppo_number = str(identity_data.get("ppo_number") or identity_data.get("ppoNumber") or "").strip()
        full_name = _normalize_text(identity_data.get("full_name"))
        department = _normalize_text(identity_data.get("department"))

        if not all([aadhaar_number, ppo_number, full_name, department]):
            return False, None, "Missing required identity fields."

        aadhaar_query_values = _digit_query_values(aadhaar_number)
        aadhaar_last4 = aadhaar_number[-4:] if aadhaar_number else ""
        aadhaar_last4_values = _digit_query_values(aadhaar_last4)

        employee = self.db["govtlist"].find_one({
            "$or": [
                {"identity.aadhaarNumber": {"$in": aadhaar_query_values}},
                {"identity.aadhaar_number": {"$in": aadhaar_query_values}},
                {"identity.aadhaarLast4": {"$in": aadhaar_last4_values + aadhaar_query_values}},
                {"identity.aadhaar_last4": {"$in": aadhaar_last4_values + aadhaar_query_values}},
                {"aadhaar_number": {"$in": aadhaar_query_values}},
                {"aadhaar_last4": {"$in": aadhaar_last4_values + aadhaar_query_values}},
            ]
        })

        if not employee:
            return False, None, "No matching government employee record found."

        official_name = _normalize_text(_first_present(employee, "name", "profile.fullName", "profile.full_name", "auth.name"))
        if not official_name:
            official_name = _normalize_text(_first_present(employee, "auth.email"))
        official_department = _normalize_text(_first_present(employee, "department", "pension.retiredDepartment", "pension.department"))

        if official_name != full_name:
            return False, None, "Name does not match official records."
        official_ppo = str(_first_present(employee, "auth.ppoNumber", "auth.ppo_number", "ppoNumber", "ppo_number")).strip()
        if official_ppo and official_ppo != ppo_number:
            return False, None, "PPO number does not match official records."
        if official_department != department:
            return False, None, "Department does not match official records."

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
        identifier = str(employee_id).strip()
        result = self.db["govtlist"].update_one(
            {
                "$or": [
                    {"employee_id": identifier},
                    {"aadhaar_number": identifier},
                    {"aadhaar_last4": identifier[-4:] if len(identifier) >= 4 else identifier},
                    {"identity.aadhaarNumber": identifier},
                    {"identity.aadhaar_number": identifier},
                    {"identity.aadhaarLast4": identifier[-4:] if len(identifier) >= 4 else identifier},
                    {"identity.aadhaar_last4": identifier[-4:] if len(identifier) >= 4 else identifier},
                ]
            },
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
                {"auth.phone": {"$in": mobile_variants}},
                {"auth.phone_number": {"$in": mobile_variants}},
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
            {"auth.email": search_regex},
            {"auth.phone": search_regex},
            {"auth.ppoNumber": search_regex},
            {"employee_id": search_regex},
            {"email": search_regex},
            {"department": search_regex},
            {"pension.retiredDepartment": search_regex},
            {"designation": search_regex},
        ]

        digit_values = _digit_query_values(query)
        if digit_values:
            or_query.extend([
                {"auth.phone": {"$in": digit_values}},
                {"auth.phone_number": {"$in": digit_values}},
                {"phone": {"$in": digit_values}},
                {"phone_number": {"$in": digit_values}},
                {"mobile": {"$in": digit_values}},
                {"identity.aadhaarNumber": {"$in": digit_values}},
                {"identity.aadhaarLast4": {"$in": _digit_query_values(str(query)[-4:])}},
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

    def get_basic_employee_data(self):
        """
        Retrieves only Aadhaar number, name, and department for all government employees.
        """
        projection = {
            "_id": 0,
            "auth.phone": 1,
            "auth.email": 1,
            "auth.ppoNumber": 1,
            "identity.aadhaarNumber": 1,
            "identity.aadhaar_number": 1,
            "identity.aadhaarLast4": 1,
            "identity.aadhaar_last4": 1,
            "name": 1,
            "profile.fullName": 1,
            "profile.full_name": 1,
            "department": 1,
            "pension.designation": 1,
            "pension.retiredDepartment": 1,
            "pension.department": 1,
        }
        return list(self.db["govtlist"].find({}, projection))


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


def get_employee_by_ppo(ppo_number: object):
    return _repo().get_employee_by_ppo(ppo_number)


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
