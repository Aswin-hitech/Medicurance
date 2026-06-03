"""
services/rag_service.py
RAG + AI validation with local FAISS retrieval and safe fallbacks.
"""
from __future__ import annotations

import json
import logging
import math
import pickle
import re
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, Iterable, List

from config.settings import Config
from services.llm_service import ask_llm

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
INDEX_FILENAME = "index.faiss"
METADATA_FILENAME = "chunks.pkl"


@dataclass
class RuleChunk:
    text: str
    source_document: str
    page: int
    chunk_id: str


def _vector_dir() -> Path:
    return Path(Config.VECTOR_DB_PATH)


def _pdf_paths() -> List[Path]:
    return [
        Path(Config.ANNEXURE_PATH),
        Path(getattr(Config, "ANNEXURE_IA_PATH", "resources/annexures/annexure_IA.pdf")),
    ]


def _dependencies_available() -> bool:
    missing = [name for name in ("fitz", "sentence_transformers", "faiss") if find_spec(name) is None]
    if missing:
        logger.warning("[RAG] Missing packages: %s", ", ".join(missing))
        return False
    return True


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def _extract_pdf_text(pdf_path: Path) -> List[Dict[str, Any]]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    import fitz

    pages = []
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            text = _clean_text(page.get_text("text"))
            if text:
                pages.append({
                    "text": text,
                    "source_document": pdf_path.name,
                    "page": page_index,
                })
    return pages


def _split_page_text(text: str, chunk_size: int = 900, overlap: int = 150) -> Iterable[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= chunk_size:
            current = f"{current} {sentence}".strip()
            continue
        if current:
            chunks.append(current)
        current = sentence

    if current:
        chunks.append(current)

    for chunk in chunks:
        if len(chunk) <= chunk_size:
            yield chunk
            continue
        start = 0
        while start < len(chunk):
            yield chunk[start:start + chunk_size].strip()
            start += max(1, chunk_size - overlap)


def _load_chunks_from_pdfs() -> List[RuleChunk]:
    chunks: List[RuleChunk] = []
    for pdf_path in _pdf_paths():
        for page in _extract_pdf_text(pdf_path):
            for chunk_number, chunk_text in enumerate(_split_page_text(page["text"]), start=1):
                if len(chunk_text) < 40:
                    continue
                chunks.append(RuleChunk(
                    text=chunk_text,
                    source_document=page["source_document"],
                    page=page["page"],
                    chunk_id=f"{page['source_document']}:p{page['page']}:c{chunk_number}",
                ))
    return chunks


def _embedding_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def _normalise_vectors(vectors):
    import numpy as np

    vectors = np.asarray(vectors, dtype="float32")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def _index_path() -> Path:
    return _vector_dir() / INDEX_FILENAME


def _metadata_path() -> Path:
    return _vector_dir() / METADATA_FILENAME


def vector_store_exists() -> bool:
    return _index_path().exists() and _metadata_path().exists()


def build_vector_db() -> bool:
    """Build a FAISS vector database from annexure_I.pdf and annexure_IA.pdf."""
    if not _dependencies_available():
        return False

    try:
        import faiss

        chunks = _load_chunks_from_pdfs()
        if not chunks:
            logger.warning("[RAG] No text chunks extracted from annexure PDFs.")
            return False

        model = _embedding_model()
        vectors = model.encode([chunk.text for chunk in chunks], show_progress_bar=False)
        vectors = _normalise_vectors(vectors)

        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)

        vector_dir = _vector_dir()
        vector_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(_index_path()))
        with _metadata_path().open("wb") as handle:
            pickle.dump(chunks, handle)

        logger.info("[RAG] Built FAISS vector DB at %s with %s chunks.", vector_dir, len(chunks))
        return True
    except Exception as exc:
        logger.warning("[RAG] Vector DB build failed: %s", exc)
        return False


def _load_vector_store():
    if not vector_store_exists():
        logger.warning("[RAG] Vector DB missing at %s. Run build_vector.py.", _vector_dir())
        return None, []
    if not _dependencies_available():
        return None, []

    try:
        import faiss

        index = faiss.read_index(str(_index_path()))
        with _metadata_path().open("rb") as handle:
            chunks = pickle.load(handle)
        return index, chunks
    except Exception as exc:
        logger.warning("[RAG] Failed to load vector DB: %s", exc)
        return None, []


def _confidence_from_score(score: float) -> float:
    if math.isnan(score):
        return 0.0
    return round(max(0.0, min(1.0, (float(score) + 1.0) / 2.0)), 4)


def retrieve_rules(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Retrieve matching government rule chunks.

    Returns dictionaries shaped as:
    {"matched_rule": "...", "source_document": "...", "confidence": 0.0}
    """
    query = str(query or "").strip()
    if not query:
        return []

    index, chunks = _load_vector_store()
    if index is None or not chunks:
        return []

    try:
        model = _embedding_model()
        query_vector = _normalise_vectors(model.encode([query], show_progress_bar=False))
        scores, indexes = index.search(query_vector, min(k, len(chunks)))
    except Exception as exc:
        logger.warning("[RAG] Rule retrieval failed: %s", exc)
        return []

    results = []
    for score, chunk_index in zip(scores[0], indexes[0]):
        if chunk_index < 0 or chunk_index >= len(chunks):
            continue
        chunk = chunks[int(chunk_index)]
        results.append({
            "matched_rule": chunk.text,
            "source_document": f"{chunk.source_document} page {chunk.page}",
            "confidence": _confidence_from_score(float(score)),
            "chunk_id": chunk.chunk_id,
        })
    return results


REQUIRED_KEYS = {
    "eligibility", "confidence", "risk_level", "fraud_score",
    "hospital_verified", "reasoning", "recommended_action",
    "fraud_flags", "missing_documents", "amount_analysis",
}

DEFAULTS = {
    "eligibility": "Unknown",
    "confidence": 0.0,
    "risk_level": "High",
    "fraud_score": 0.5,
    "hospital_verified": False,
    "reasoning": "AI analysis could not be completed.",
    "recommended_action": "Review",
    "fraud_flags": [],
    "missing_documents": [],
    "amount_analysis": {"claimed": 0, "expected_range": "N/A", "status": "anomalous"},
}


def _clean_llm_response(raw: str) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE).strip()
    raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()
    return raw


def _recover_json(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def _validate_schema(result: dict) -> dict:
    for key, default in DEFAULTS.items():
        if key not in result or result[key] is None:
            result[key] = default

    try:
        result["confidence"] = float(result["confidence"])
        if result["confidence"] > 1.0:
            result["confidence"] = result["confidence"] / 100.0
        result["confidence"] = round(min(max(result["confidence"], 0.0), 1.0), 4)
    except (TypeError, ValueError):
        result["confidence"] = 0.0

    try:
        result["fraud_score"] = float(result["fraud_score"])
        if result["fraud_score"] > 1.0:
            result["fraud_score"] = result["fraud_score"] / 100.0
        result["fraud_score"] = round(min(max(result["fraud_score"], 0.0), 1.0), 4)
    except (TypeError, ValueError):
        result["fraud_score"] = 0.5

    if not isinstance(result["fraud_flags"], list):
        result["fraud_flags"] = []
    if not isinstance(result["missing_documents"], list):
        result["missing_documents"] = []
    if not isinstance(result["amount_analysis"], dict):
        result["amount_analysis"] = DEFAULTS["amount_analysis"]

    result["eligibility"] = str(result["eligibility"]).strip().title()
    result["risk_level"] = str(result["risk_level"]).strip().title()
    result["recommended_action"] = str(result["recommended_action"]).strip().title()
    result["hospital_verified"] = bool(result["hospital_verified"])
    return result


def rag_validate(bill_text: str, entities: dict | None = None) -> dict:
    """
    Validate a medical bill against government scheme rules using RAG + Groq LLM.
    """
    if not bill_text or not bill_text.strip():
        logger.warning("[RAG] Empty bill text - returning high-risk defaults.")
        return _validate_schema({**DEFAULTS, "fraud_flags": ["Empty bill text - cannot validate"]})

    docs = retrieve_rules(bill_text[:1000], k=5)
    if not docs:
        return _validate_schema({
            **DEFAULTS,
            "eligibility": "Unknown",
            "confidence": 0.0,
            "reason": "RAG unavailable",
            "reasoning": "RAG unavailable",
            "fraud_flags": ["RAG unavailable"],
        })

    context = "\n\n".join(
        f"Source: {doc['source_document']}\nRule: {doc['matched_rule']}"
        for doc in docs
    )

    entity_ctx = ""
    if entities:
        parts = []
        if entities.get("patient_name"):
            parts.append(f"Patient: {entities['patient_name']}")
        if entities.get("hospital_name"):
            parts.append(f"Hospital: {entities['hospital_name']}")
        if entities.get("claim_amount"):
            parts.append(f"Amount: Rs {entities['claim_amount']}")
        if entities.get("admission_date"):
            parts.append(f"Admission: {entities['admission_date']}")
        if entities.get("discharge_date"):
            parts.append(f"Discharge: {entities['discharge_date']}")
        if entities.get("doctor_name"):
            parts.append(f"Doctor: {entities['doctor_name']}")
        if parts:
            entity_ctx = "Extracted Entities:\n" + "\n".join(parts)

    prompt = f"""You are an AI system auditing government medical reimbursement claims in India.
Validate the medical bill strictly against the government scheme rules provided.

GOVERNMENT SCHEME RULES:
{context}

{entity_ctx}

MEDICAL BILL TEXT:
{bill_text[:3000]}

Return ONLY valid JSON with NO markdown and NO explanation:
{{
  "eligibility": "Eligible" or "Not Eligible",
  "confidence": <float 0.0 to 1.0>,
  "risk_level": "Low" or "Medium" or "High",
  "fraud_score": <float 0.0 to 1.0>,
  "hospital_verified": <true or false>,
  "amount_analysis": {{
    "claimed": <number>,
    "expected_range": "<string>",
    "status": "normal" or "anomalous"
  }},
  "missing_documents": ["<list of missing items>"],
  "fraud_flags": ["<list of detected fraud indicators>"],
  "reasoning": "<detailed explanation of eligibility decision and risk assessment>",
  "recommended_action": "Approve" or "Reject" or "Review"
}}"""

    try:
        raw_response = ask_llm(prompt, json_mode=True)
    except Exception as exc:
        logger.error("[RAG] LLM call failed: %s", exc)
        return _validate_schema({**DEFAULTS, "fraud_flags": [f"LLM call error: {str(exc)[:100]}"]})

    cleaned = _clean_llm_response(raw_response)
    result = _recover_json(cleaned)
    if result is None:
        logger.error("[RAG] Cannot parse LLM response. Raw: %s", raw_response[:300])
        return _validate_schema({
            **DEFAULTS,
            "fraud_flags": ["AI Parsing Error - manual review required"],
            "reasoning": f"Failed to parse AI response. Raw: {raw_response[:200]}",
        })

    validated = _validate_schema(result)
    logger.info(
        "[RAG] Validation complete | eligibility=%s | confidence=%s | risk=%s",
        validated["eligibility"],
        validated["confidence"],
        validated["risk_level"],
    )
    return validated
