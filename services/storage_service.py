"""
services/storage_service.py
Phase 1 — Supabase Storage Migration
Replaces Cloudinary entirely with Supabase Storage.
"""
import os
import time
import logging
import mimetypes
import tempfile
from pathlib import Path

from config.settings import Config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Lazy Supabase Client (initialised on first use)
# ─────────────────────────────────────────────
_supabase_client = None


def _get_client():
    """Return (or lazily create) the Supabase client."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    if not Config.SUPABASE_URL or not Config.SUPABASE_SECRET_KEY:
        logger.warning("[Storage] Supabase credentials not configured — uploads will be skipped.")
        return None

    try:
        from supabase import create_client
        # Use service_role key so we can write to the bucket without user auth
        _supabase_client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SECRET_KEY)
        logger.info("[Storage] Supabase client initialised successfully.")
        return _supabase_client
    except Exception as exc:
        logger.error(f"[Storage] Failed to initialise Supabase client: {exc}")
        return None


# ─────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────

def _build_storage_path(filename: str) -> str:
    """
    Build a unique storage path inside the bucket.
    Format: claims/<epoch_ms>_<sanitised_filename>
    """
    epoch_ms = int(time.time() * 1000)
    safe_name = Path(filename).name.replace(" ", "_")
    return f"claims/{epoch_ms}_{safe_name}"


def _detect_mime(file_path: str) -> str:
    extension = Path(file_path).suffix.lower()
    if extension == ".pdf":
        return "application/pdf"
    if extension in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if extension == ".png":
        return "image/png"

    mime, _ = mimetypes.guess_type(file_path)
    return mime or "application/octet-stream"


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def upload_file(file_path: str, filename: str | None = None, bucket_name: str | None = None) -> str | None:
    """
    Upload a file to Supabase Storage.

    Args:
        file_path:  Absolute path to the local temp file.
        filename:   Optional override for the stored filename.
                    Defaults to the basename of file_path.

    Returns:
        Secure public URL string on success, None on failure.
    """
    filename = filename or os.path.basename(file_path)
    bucket_name = bucket_name or Config.SUPABASE_BILL_BUCKET
    storage_path = _build_storage_path(filename)

    client = _get_client()
    if client is None:
        logger.warning("[Storage] No Supabase client — upload skipped.")
        return None

    last_error = None
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            mime_type = _detect_mime(file_path)
            bucket = client.storage.from_(bucket_name)
            # Exponential backoff makes temporary storage/network errors survivable.
            attempt_path = storage_path if attempt == 0 else _build_storage_path(f"retry{attempt}_{filename}")
            bucket.upload(
                path=attempt_path,
                file=file_bytes,
                file_options={
                    "content-type": mime_type,
                    "cache-control": "3600",
                    "upsert": "false" if attempt == 0 else "true",
                },
            )
            public_url = generate_public_url(attempt_path, bucket_name=bucket_name)
            logger.info(f"[Storage] Uploaded -> {attempt_path}")
            return public_url
        except Exception as exc:
            last_error = exc
            logger.warning("[Storage] Upload attempt %s failed for %s: %s", attempt + 1, filename, exc)
            time.sleep(2 ** attempt)

    logger.error(f"[Storage] Upload failed after retries: {last_error}")
    return fallback_upload(file_path, filename, bucket_name=bucket_name)

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        mime_type = _detect_mime(file_path)
        bucket = client.storage.from_(Config.SUPABASE_BUCKET)
        bucket.upload(
            path=storage_path,
            file=file_bytes,
            file_options={
                "content-type": mime_type,
                "cache-control": "3600",
                "upsert": "false",
            },
        )
        public_url = generate_public_url(storage_path)
        logger.info(f"[Storage] Uploaded → {storage_path}")
        return public_url

    except Exception as exc:
        logger.error(f"[Storage] Upload failed on first attempt: {exc}")
        return fallback_upload(file_path, filename)


def fallback_upload(file_path: str, filename: str, bucket_name: str | None = None) -> str | None:
    """
    Retry upload once after a short delay.
    Logs a permanent failure record if the retry also fails.
    """
    logger.warning(f"[Storage] Retrying upload for {filename} in 2 s …")
    time.sleep(2)

    client = _get_client()
    if client is None:
        _log_upload_failure(filename, "Supabase client unavailable")
        return None

    storage_path = _build_storage_path(f"retry_{filename}")
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        mime_type = _detect_mime(file_path)
        bucket_name = bucket_name or Config.SUPABASE_BILL_BUCKET
        bucket = client.storage.from_(bucket_name)
        bucket.upload(
            path=storage_path,
            file=file_bytes,
            file_options={"content-type": mime_type, "upsert": "true"},
        )
        public_url = generate_public_url(storage_path, bucket_name=bucket_name)
        logger.info(f"[Storage] Retry upload succeeded -> {storage_path}")
        return public_url

    except Exception as exc:
        _log_upload_failure(filename, str(exc))
        return None


def delete_file(storage_path: str, bucket_name: str | None = None) -> bool:
    """
    Remove a file from the Supabase bucket.

    Args:
        storage_path: The path inside the bucket (e.g. 'claims/1234_bill.pdf').

    Returns:
        True on success, False otherwise.
    """
    client = _get_client()
    if client is None:
        return False
    try:
        bucket_name = bucket_name or Config.SUPABASE_BILL_BUCKET
        client.storage.from_(bucket_name).remove([storage_path])
        logger.info(f"[Storage] Deleted → {storage_path}")
        return True
    except Exception as exc:
        logger.error(f"[Storage] Delete failed for {storage_path}: {exc}")
        return False


def generate_public_url(storage_path: str, bucket_name: str | None = None) -> str | None:
    """
    Generate a public URL for the given storage path.
    Requires the bucket to have 'Public' read access in Supabase.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        bucket_name = bucket_name or Config.SUPABASE_BILL_BUCKET
        result = client.storage.from_(bucket_name).get_public_url(storage_path)
        if isinstance(result, str):
            url = result
        elif isinstance(result, dict):
            url = result.get("publicUrl") or result.get("public_url") or result.get("url")
        else:
            url = getattr(result, "publicUrl", None) or getattr(result, "public_url", None) or getattr(result, "url", None)

        if not url:
            return None

        if storage_path.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
            url = f"{url}?download=false"

        return url
    except Exception as exc:
        logger.error(f"[Storage] URL generation failed: {exc}")
        return None


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _log_upload_failure(filename: str, reason: str):
    """Write a failure record to MongoDB audit_logs."""
    try:
        from database.mongo_client import db
        from datetime import datetime, timezone
        db["audit_logs"].insert_one({
            "actor": "system",
            "action": "upload_failure",
            "description": f"Supabase upload permanently failed for '{filename}': {reason}",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception:
        pass  # Never crash the main flow due to logging
