from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database.mongo_client import claim_versions_collection
from utils.status_utils import normalize_claim_status

class ClaimRepository:
    def __init__(self, db):
        self.db = db

    def create_claim(self, data: Dict[str, Any]):
        now = datetime.now(timezone.utc).isoformat()
        payload = dict(data)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        payload.setdefault("is_deleted", False)
        payload.setdefault("version", 1)
        self.db["claims"].insert_one(payload)
        self._create_version_snapshot(payload, "created")
        return payload

    def _create_version_snapshot(self, claim: Dict[str, Any], event: str, actor: str = "system", notes: str = ""):
        snapshot = dict(claim)
        snapshot["event"] = event
        snapshot["actor"] = actor
        snapshot["notes"] = notes
        snapshot["created_at"] = datetime.now(timezone.utc).isoformat()
        claim_versions_collection.insert_one(snapshot)

    def get_claim_by_id(self, claim_id: str) -> Optional[Dict[str, Any]]:
        return self.db["claims"].find_one({"claim_id": claim_id, "is_deleted": {"$ne": True}})

    def update_claim(self, claim_id: str, updates: Dict[str, Any], actor: str = "system", notes: str = ""):
        claim = self.get_claim_by_id(claim_id)
        if not claim:
            return None
        next_version = int(claim.get("version", 1) or 1) + 1
        updates = dict(updates)
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        updates["version"] = next_version
        self.db["claims"].update_one({"claim_id": claim_id}, {"$set": updates})
        updated_claim = self.get_claim_by_id(claim_id) or {**claim, **updates}
        self._create_version_snapshot(updated_claim, "updated", actor=actor, notes=notes)
        return updated_claim

    def soft_delete_claim(self, claim_id: str, actor: str = "system"):
        claim = self.get_claim_by_id(claim_id)
        if not claim:
            return None
        now = datetime.now(timezone.utc).isoformat()
        self.db["claims"].update_one(
            {"claim_id": claim_id},
            {"$set": {"is_deleted": True, "deleted_at": now, "updated_at": now, "deleted_by": actor}},
        )
        claim["is_deleted"] = True
        claim["deleted_at"] = now
        self._create_version_snapshot(claim, "deleted", actor=actor, notes="Soft delete")
        return claim

    def get_claims_by_user(self, mobile: object, skip: int = 0, limit: int = 0, sort_field: str = "created_at", sort_order: int = -1):
        cursor = self.db["claims"].find({"mobile": mobile, "is_deleted": {"$ne": True}}).sort(sort_field, sort_order)
        if skip:
            cursor = cursor.skip(int(skip))
        if limit:
            cursor = cursor.limit(int(limit))
        claims = list(cursor)
        for claim in claims:
            claim["status"] = normalize_claim_status(claim.get("status"))
        return claims

    def get_all_claims(self, skip: int = 0, limit: int = 0, sort_field: str = "created_at", sort_order: int = -1):
        cursor = self.db["claims"].find({"is_deleted": {"$ne": True}}).sort(sort_field, sort_order)
        if skip:
            cursor = cursor.skip(int(skip))
        if limit:
            cursor = cursor.limit(int(limit))
        claims = list(cursor)
        for claim in claims:
            claim["status"] = normalize_claim_status(claim.get("status"))
        return claims


_default_repo: ClaimRepository | None = None


def _repo() -> ClaimRepository:
    global _default_repo
    if _default_repo is None:
        from database.mongo_client import db

        _default_repo = ClaimRepository(db)
    return _default_repo


def create_claim(data: Dict[str, Any]):
    return _repo().create_claim(data)


def get_claims_by_user(mobile: object, skip: int = 0, limit: int = 0, sort_field: str = "created_at", sort_order: int = -1):
    return _repo().get_claims_by_user(mobile, skip=skip, limit=limit, sort_field=sort_field, sort_order=sort_order)


def get_all_claims(skip: int = 0, limit: int = 0, sort_field: str = "created_at", sort_order: int = -1):
    return _repo().get_all_claims(skip=skip, limit=limit, sort_field=sort_field, sort_order=sort_order)
