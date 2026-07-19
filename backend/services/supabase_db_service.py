from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, Iterable

from config.settings import Config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client():
    api_key = (
        getattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "")
        or Config.SUPABASE_SECRET_KEY
        or Config.SUPABASE_API_KEY
    )
    if not Config.SUPABASE_URL or not api_key:
        return None

    try:
        from supabase import create_client

        return create_client(Config.SUPABASE_URL, api_key)
    except Exception as exc:
        logger.warning("[SupabaseDB] client init failed: %s", exc)
        return None


def upsert_annexure_chunk(row: Dict[str, Any]) -> bool:
    client = _client()
    if client is None:
        logger.warning("[SupabaseDB] client unavailable; skipping chunk upsert.")
        return False

    payload = dict(row)
    payload.setdefault("metadata", {})
    payload.setdefault("created_at", __import__("datetime").datetime.utcnow().isoformat())
    try:
        client.table("annexure_chunks").upsert(payload, on_conflict="file_path,chunk_index").execute()
        return True
    except Exception as exc:
        logger.warning("[SupabaseDB] annexure_chunks upsert failed: %s", exc)
        return False


def bulk_upsert_annexure_chunks(rows: Iterable[Dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        if upsert_annexure_chunk(row):
            count += 1
    return count

