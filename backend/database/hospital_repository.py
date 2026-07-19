from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from config.settings import Config
from database.mongo_client import hospitals_collection
from utils.cache import cache_manager, ttl_cache


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _hospital_query(identifier: Any) -> Dict[str, Any]:
    raw = _normalize_text(identifier)
    if not raw:
        return {}
    if raw.isdigit():
        return {"$or": [{"nhisCode": int(raw)}, {"hospitalId": raw}]}
    return {
        "$or": [
            {"hospitalId": raw},
            {"name": {"$regex": f"^{raw}$", "$options": "i"}},
            {"email": raw.lower()},
        ]
    }


def _build_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    payload = dict(data or {})
    payload.pop("_id", None)
    payload.setdefault("hospitalId", payload.get("hospitalId") or payload.get("hospital_id") or payload.get("name"))
    if payload.get("nhisCode") is not None:
        try:
            payload["nhisCode"] = int(payload["nhisCode"])
        except Exception:
            payload["nhisCode"] = 0
    payload.setdefault("name", "")
    payload.setdefault("district", "")
    payload.setdefault("cluster", "")
    payload.setdefault("address", "")
    payload.setdefault("pincode", "")
    payload.setdefault("phone", [])
    payload.setdefault("email", "")
    payload.setdefault("location", {"type": "Point", "coordinates": [0.0, 0.0]})
    payload.setdefault("specialties", [])
    payload.setdefault("facilities", [])
    payload.setdefault("schemes", ["TN NHIS 2026"])
    payload.setdefault("cashless", True)
    payload.setdefault("timings", {"opd": "", "emergency": True})
    payload.setdefault("status", "active")
    payload.setdefault("createdAt", payload.get("createdAt") or now)
    payload["updatedAt"] = now

    if isinstance(payload.get("phone"), str):
        payload["phone"] = [payload["phone"]]
    if not isinstance(payload.get("phone"), list):
        payload["phone"] = []
    if not isinstance(payload.get("specialties"), list):
        payload["specialties"] = []
    if not isinstance(payload.get("facilities"), list):
        payload["facilities"] = []
    if not isinstance(payload.get("schemes"), list):
        payload["schemes"] = [str(payload.get("schemes"))]
    if not isinstance(payload.get("location"), dict):
        payload["location"] = {"type": "Point", "coordinates": [0.0, 0.0]}
    if not isinstance(payload.get("timings"), dict):
        payload["timings"] = {"opd": "", "emergency": True}
    return payload


class HospitalRepository:
    def __init__(self, db):
        self.db = db

    def upsert_hospital(self, hospital_data: Dict[str, Any]):
        payload = _build_payload(hospital_data)
        query = {"$or": []}
        if payload.get("hospitalId"):
            query["$or"].append({"hospitalId": payload["hospitalId"]})
        if payload.get("nhisCode") not in (None, ""):
            query["$or"].append({"nhisCode": payload["nhisCode"]})
        if payload.get("name"):
            query["$or"].append({"name": {"$regex": f"^{_normalize_text(payload['name'])}$", "$options": "i"}})
        if not query["$or"]:
            raise ValueError("hospitalId, nhisCode, or name is required")

        self.db["hospitals"].update_one(query, {"$set": payload}, upsert=True)
        cache_manager.delete(f"hospital:{_normalize_key(payload.get('hospitalId') or payload.get('name'))}")
        return self.db["hospitals"].find_one(query)

    def add_hospital(self, name, district="", state="", network=True):
        payload = _build_payload({
            "hospitalId": name,
            "name": name,
            "district": district,
            "cluster": state,
            "cashless": bool(network),
        })
        return self.upsert_hospital(payload)

    def get_hospital_by_name(self, name):
        if not name:
            return None
        query = {"$or": [{"name": {"$regex": f"^{_normalize_text(name)}$", "$options": "i"}}, {"hospitalId": name}]}
        return self.db["hospitals"].find_one({**query, "status": {"$ne": "deleted"}})

    def get_hospital_by_identifier(self, identifier):
        query = _hospital_query(identifier)
        if not query:
            return None
        return self.db["hospitals"].find_one({**query, "status": {"$ne": "deleted"}})

    def get_all_hospitals(self):
        return list(self.db["hospitals"].find({"status": {"$ne": "deleted"}}).sort("name", 1))

    def remove_hospital(self, identifier):
        now = datetime.now(timezone.utc).isoformat()
        query = _hospital_query(identifier)
        cache_manager.delete(f"hospital:{_normalize_key(identifier)}")
        return self.db["hospitals"].update_one(query, {"$set": {"status": "deleted", "updatedAt": now}})

    def update_hospital_network(self, identifier, status):
        now = datetime.now(timezone.utc).isoformat()
        query = _hospital_query(identifier)
        cache_manager.delete(f"hospital:{_normalize_key(identifier)}")
        return self.db["hospitals"].update_one(query, {"$set": {"cashless": bool(status), "updatedAt": now}})

    def verify_hospital(self, hospital_name):
        hospital = self.get_hospital_by_identifier(hospital_name)
        if hospital:
            return {
                "exists": True,
                "verified": True,
                "network": bool(hospital.get("cashless", False)),
                "hospitalId": hospital.get("hospitalId"),
                "nhisCode": hospital.get("nhisCode"),
                "name": hospital.get("name"),
                "district": hospital.get("district"),
                "cluster": hospital.get("cluster"),
            }
        return {
            "exists": False,
            "verified": False,
            "network": False,
        }


_default_repo: HospitalRepository | None = None


def _repo() -> HospitalRepository:
    global _default_repo
    if _default_repo is None:
        from database.mongo_client import db

        _default_repo = HospitalRepository(db)
    return _default_repo


def add_hospital(name, city, state, network=True):
    return _repo().add_hospital(name=name, district=city, state=state, network=network)


def upsert_hospital(hospital_data: Dict[str, Any]):
    return _repo().upsert_hospital(hospital_data)


def get_hospital_by_name(name):
    return _repo().get_hospital_by_name(name)


def get_hospital_by_identifier(identifier):
    return _repo().get_hospital_by_identifier(identifier)


def get_all_hospitals():
    return _repo().get_all_hospitals()


def remove_hospital(name):
    return _repo().remove_hospital(name)


def update_hospital_network(name, status):
    return _repo().update_hospital_network(name, status)


def verify_hospital(hospital_name):
    return _repo().verify_hospital(hospital_name)
