"""
services/conversation_memory_service.py
Persistent multi-turn conversation memory for the Alchemyst AI chatbot.

Stores conversation state in MongoDB (chat_conversations collection).
Each conversation document tracks:
  - conversation_id (UUID per session)
  - user_id
  - messages ([{role, content, timestamp}])
  - rag_chunks_used (chunk IDs retrieved per turn)
  - last_medical_topic (lightweight keyword extraction)
  - language
  - ai_context (any extra metadata for Alchemyst)
  - created_at / updated_at

Uses the existing MongoDB connection. Falls back gracefully if MongoDB is unavailable.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database.mongo_client import chat_conversations_collection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Medical topic keywords for lightweight extraction
# ---------------------------------------------------------------------------
_MEDICAL_KEYWORDS = [
    "annexure", "eligibility", "claim", "reimbursement", "hospital", "surgery",
    "treatment", "diagnosis", "medicine", "medicine", "procedure", "discharge",
    "admission", "doctor", "patient", "fraud", "trust score", "ocr", "bill",
    "insurance", "nhis", "cmchis", "pension", "beneficiary", "scheme",
    "appendix", "rule", "regulation", "document", "policy", "coverage",
    # Tamil keywords
    "மருத்துவ", "கோரிக்கை", "மருத்துவமனை", "சிகிச்சை", "நோய்",
]

_MAX_MESSAGES_PER_CONVERSATION = 40  # keep last 40 messages (20 turns)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_medical_topic(text: str) -> str:
    """
    Lightweight keyword extraction to track the last medical topic discussed.
    Returns the first matching keyword found in the text, or empty string.
    """
    text_lower = (text or "").lower()
    for kw in _MEDICAL_KEYWORDS:
        if kw in text_lower:
            return kw
    return ""


def _trim_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only the last _MAX_MESSAGES_PER_CONVERSATION messages."""
    if len(messages) > _MAX_MESSAGES_PER_CONVERSATION:
        return messages[-_MAX_MESSAGES_PER_CONVERSATION:]
    return messages


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_conversation(
    conversation_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Load a conversation from MongoDB by conversation_id.
    If not found, returns a new empty conversation dict.

    Args:
        conversation_id: UUID string identifying the conversation session.
        user_id:         Optional user identifier for ownership verification.

    Returns:
        Dict with keys: conversation_id, user_id, messages, rag_chunks_used,
                        last_medical_topic, language, ai_context, created_at, updated_at.
    """
    if not conversation_id:
        return _new_conversation(user_id)

    try:
        query: Dict[str, Any] = {"conversation_id": conversation_id}
        if user_id:
            query["user_id"] = user_id

        doc = chat_conversations_collection.find_one(query)
        if doc:
            doc.pop("_id", None)
            logger.debug(
                "[ConvMemory] Loaded conversation | id=%s | messages=%d",
                conversation_id, len(doc.get("messages", [])),
            )
            return doc

    except Exception as exc:
        logger.warning("[ConvMemory] Failed to load conversation %s: %s", conversation_id, exc)

    return _new_conversation(user_id, conversation_id=conversation_id)


def save_conversation(
    conversation_id: str,
    user_id: Optional[str],
    messages: List[Dict[str, Any]],
    *,
    rag_chunks_used: Optional[List[str]] = None,
    language: str = "en-IN",
    ai_context: Optional[Dict[str, Any]] = None,
    last_medical_topic: str = "",
) -> bool:
    """
    Upsert conversation state to MongoDB.

    Args:
        conversation_id:   UUID session identifier.
        user_id:           User identifier.
        messages:          Full message list [{role, content, timestamp}].
        rag_chunks_used:   Chunk IDs retrieved in this turn.
        language:          Language code.
        ai_context:        Extra metadata (claim_id, trust_score, etc.).
        last_medical_topic: Extracted topic for context retention.

    Returns:
        True on success, False on failure (failure is logged, not raised).
    """
    if not conversation_id:
        return False

    trimmed_messages = _trim_messages(messages)
    now = _utcnow()

    document = {
        "conversation_id": conversation_id,
        "user_id": user_id or "",
        "messages": trimmed_messages,
        "rag_chunks_used": list(rag_chunks_used or []),
        "language": language,
        "ai_context": dict(ai_context or {}),
        "last_medical_topic": last_medical_topic or "",
        "updated_at": now,
    }

    try:
        result = chat_conversations_collection.update_one(
            {"conversation_id": conversation_id},
            {
                "$set": document,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        logger.debug(
            "[ConvMemory] Saved conversation | id=%s | messages=%d | upserted=%s",
            conversation_id,
            len(trimmed_messages),
            result.upserted_id is not None,
        )
        return True
    except Exception as exc:
        logger.warning("[ConvMemory] Failed to save conversation %s: %s", conversation_id, exc)
        return False


def append_turn(
    conversation: Dict[str, Any],
    user_message: str,
    assistant_message: str,
    rag_chunk_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Append a user+assistant turn to an in-memory conversation dict.
    Call save_conversation() afterwards to persist.

    Args:
        conversation:     The conversation dict returned by load_conversation().
        user_message:     The user's query text.
        assistant_message: The assistant's answer text.
        rag_chunk_ids:    Chunk IDs used in this turn.

    Returns:
        Updated conversation dict (mutated in place and returned).
    """
    now = _utcnow()
    messages: List[Dict[str, Any]] = conversation.setdefault("messages", [])

    messages.append({"role": "user", "content": user_message, "timestamp": now})
    messages.append({"role": "assistant", "content": assistant_message, "timestamp": now})

    # Update topic
    topic = _extract_medical_topic(user_message)
    if topic:
        conversation["last_medical_topic"] = topic

    # Accumulate chunk IDs
    existing_chunks: List[str] = conversation.setdefault("rag_chunks_used", [])
    for cid in (rag_chunk_ids or []):
        if cid and cid not in existing_chunks:
            existing_chunks.append(cid)

    conversation["updated_at"] = now
    return conversation


def format_messages_for_alchemyst(
    conversation: Dict[str, Any],
    current_query: str,
) -> List[Dict[str, str]]:
    """
    Convert a conversation dict's message history + current query into the
    OpenAI-format messages list expected by ask_alchemyst().

    Args:
        conversation:  The conversation dict (from load_conversation).
        current_query: The new user query to append.

    Returns:
        List of {"role": ..., "content": ...} dicts.
    """
    messages: List[Dict[str, str]] = []

    stored: List[Dict[str, Any]] = conversation.get("messages", [])
    for msg in stored:
        role = msg.get("role", "user")
        content = str(msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    # Append the new user query
    messages.append({"role": "user", "content": current_query.strip()})
    return messages


def get_or_create_conversation_id(
    provided_id: Optional[str],
    user_id: Optional[str],
) -> str:
    """
    Return the provided conversation_id if valid, otherwise generate a new UUID.
    Does NOT persist anything — just ensures a valid ID exists.
    """
    cid = (provided_id or "").strip()
    if cid:
        return cid
    new_id = str(uuid.uuid4())
    logger.debug("[ConvMemory] Generated new conversation_id=%s for user=%s", new_id, user_id)
    return new_id


# ---------------------------------------------------------------------------
# Private factory
# ---------------------------------------------------------------------------
def _new_conversation(
    user_id: Optional[str],
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    now = _utcnow()
    return {
        "conversation_id": conversation_id or str(uuid.uuid4()),
        "user_id": user_id or "",
        "messages": [],
        "rag_chunks_used": [],
        "last_medical_topic": "",
        "language": "en-IN",
        "ai_context": {},
        "created_at": now,
        "updated_at": now,
    }
