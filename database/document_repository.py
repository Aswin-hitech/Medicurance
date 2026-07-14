from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class DocumentRepository:
    def __init__(self, db):
        self.db = db

    def save_document(self, data: Dict[str, Any]):
        payload = dict(data or {})
        payload.pop("_id", None)
        payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        payload.setdefault("updated_at", payload["created_at"])
        return self.db["documents"].update_one(
            {
                "owner_id": payload.get("owner_id"),
                "document_type": payload.get("document_type"),
                "storage_path": payload.get("storage_path"),
            },
            {"$set": payload},
            upsert=True,
        )

    def get_documents(self, owner_id: str | None = None, claim_id: str | None = None, document_type: str | None = None):
        query: Dict[str, Any] = {}
        if owner_id:
            query["owner_id"] = owner_id
        if claim_id:
            query["claim_id"] = claim_id
        if document_type:
            query["document_type"] = document_type
        return list(self.db["documents"].find(query))

    def get_document(self, owner_id: str, document_type: str):
        return self.db["documents"].find_one({"owner_id": owner_id, "document_type": document_type})

    def delete_document(self, owner_id: str, document_type: str):
        return self.db["documents"].delete_one({"owner_id": owner_id, "document_type": document_type})
