import logging
from pathlib import Path

from config.settings import Config

logger = logging.getLogger(__name__)


def validate_vector_store():
    vector_path = Path(Config.VECTOR_DB_PATH)
    index_path = vector_path / "index.faiss"
    metadata_path = vector_path / "chunks.pkl"
    if index_path.exists() and metadata_path.exists():
        logger.info("[RAG] Vector store ready at %s", vector_path)
        return {"ok": True, "rebuilt": False}

    if not getattr(Config, "VECTOR_AUTO_REBUILD", False):
        logger.warning("[RAG] Vector store missing at %s; run build_vector.py.", vector_path)
        return {"ok": False, "rebuilt": False, "reason": "missing"}

    try:
        from services.rag_service import build_vector_db

        if not build_vector_db():
            return {"ok": False, "rebuilt": False, "reason": "build_failed"}
        return {"ok": True, "rebuilt": True}
    except Exception as exc:
        logger.error("[RAG] Vector store rebuild failed: %s", exc)
        return {"ok": False, "rebuilt": False, "reason": str(exc)}
