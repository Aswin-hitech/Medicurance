"""
services/chat_service.py
AI Chatbot service — Alchemyst-powered conversational reasoning with persistent memory.

Pipeline:
  1. Small-talk guard (lightweight, no AI needed)
  2. Tamil → English translation for RAG embedding search (still uses ask_llm/Groq — utility only)
  3. retrieve_context_for_chat() — FAISS vector retrieval of government annexure chunks
  4. load_conversation() — fetch persistent multi-turn history from MongoDB
  5. format_messages_for_alchemyst() — build OpenAI-format message list
  6. ask_chatbot_llm() → Alchemyst AI (with Groq fallback) — conversational reasoning
  7. append_turn() + save_conversation() — persist memory to MongoDB
  8. Return (answer, used_docs, follow_up_questions)

IMPORTANT: The existing claim processing pipeline (rag_validate, ask_llm) is NOT modified.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from config.settings import Config
from services.rag_service import retrieve_context_for_chat
from services.llm_service import ask_llm, ask_chatbot_llm
from services.conversation_memory_service import (
    load_conversation,
    save_conversation,
    append_turn,
    format_messages_for_alchemyst,
    get_or_create_conversation_id,
    _extract_medical_topic,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small-talk patterns (unchanged from original)
# ---------------------------------------------------------------------------
_SMALL_TALK_PATTERNS = [
    "hi", "hello", "hey", "namaste", "thanks", "thank you", "thx",
    "வணக்கம்", "ஹாய்", "நன்றி", "hello there", "good morning", "good afternoon", "good evening"
]


def _is_small_talk(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    if any(pattern in text for pattern in _SMALL_TALK_PATTERNS):
        return len(text.split()) <= 6 or text in {
            "hi", "hello", "hey", "thanks", "thank you", "thankyou",
            "வணக்கம்", "நன்றி"
        }
    return False


def _small_talk_response(language: str, query: str) -> str:
    text = (query or "").strip().lower()
    if language == "ta-IN":
        if any(word in text for word in ["நன்றி", "thanks", "thank you", "thankyou"]):
            return "நன்றி! நான் உதவ தயாராக இருக்கிறேன். உங்கள் மருத்துவக் கோரிக்கை, தகுதி, அல்லது தேவையான ஆவணங்கள் பற்றி கேளுங்கள்."
        return "வணக்கம்! நான் உங்கள் AI பென்ஷன் உதவியாளர். உங்கள் கோரிக்கை, தகுதி, அல்லது அடுத்த படிகள் பற்றி நான் உதவ முடியும்."
    if any(word in text for word in ["நன்றி", "thanks", "thank you", "thankyou"]):
        return "You're very welcome. I'm here to help with eligibility, documents, claims, or next steps."
    return "Hello! I'm your AI Pension Assistant. I can help with eligibility, required documents, claims, and next steps."


def _default_follow_ups(language: str) -> List[str]:
    if language == "ta-IN":
        return ["தகுதி உள்ளதா?", "தேவையான ஆவணங்கள் என்ன?", "அடுத்த படி என்ன?"]
    return ["Check eligibility", "Required docs", "Next steps", "Explain my trust score"]


def _normalize_follow_ups(items: List[str], language: str) -> List[str]:
    cleaned = []
    seen = set()
    for item in items or []:
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    for fallback in _default_follow_ups(language):
        if len(cleaned) >= 4:
            break
        if fallback.lower() not in seen:
            cleaned.append(fallback)
            seen.add(fallback.lower())
    return cleaned[:4]


# ---------------------------------------------------------------------------
# Tamil → English translation for RAG search (uses Groq — utility, not chatbot)
# ---------------------------------------------------------------------------
def _translate_for_rag(query: str, language: str) -> str:
    """
    Translate a Tamil query to English for better FAISS embedding search.
    Falls back to the original query on any error.
    """
    if language != "ta-IN":
        return query
    try:
        prompt = (
            "Translate the following Tamil text to English for a search query. "
            "Return ONLY the English translation without any extra words or markdown:\n\n"
            f"{query}"
        )
        english_query = ask_llm(prompt, json_mode=False)
        if english_query and not english_query.startswith("{"):
            translated = english_query.strip()
            logger.info("[Chat] Translated Tamil query for RAG: %s", translated[:100])
            return translated
    except Exception as exc:
        logger.warning("[Chat] Tamil translation failed: %s", exc)
    return query


# ---------------------------------------------------------------------------
# Main chatbot entry point
# ---------------------------------------------------------------------------
def process_chat_query(
    query: str,
    history: Optional[List[Dict[str, str]]] = None,
    language: str = "en-IN",
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """
    Process a chat query using RAG retrieval + Alchemyst AI with persistent memory.

    Args:
        query:           The user's text question.
        history:         Legacy short-term history list [{sender, text}] from the caller.
                         Kept for backward compatibility; persistent memory takes precedence.
        language:        "en-IN" or "ta-IN".
        conversation_id: UUID identifying the conversation session (optional).
        user_id:         User identifier for memory scoping (optional).
        metadata:        Optional claim context dict for Alchemyst system prompt enrichment.

    Returns:
        Tuple of (answer_text, used_docs, follow_up_questions).
        Never raises — all errors result in a graceful fallback response.
    """
    query = (query or "").strip()

    # --- Guard: empty query ---
    if not query:
        return "Please provide a valid question.", [], _default_follow_ups(language)

    # --- Guard: small talk ---
    if _is_small_talk(query):
        logger.info("[Chat] Small-talk detected — returning canned response.")
        return _small_talk_response(language, query), [], _default_follow_ups(language)

    # --- Step 1: Translate Tamil query for RAG embedding search ---
    search_query = _translate_for_rag(query, language)

    # --- Step 2: RAG retrieval ---
    rag_chunks = retrieve_context_for_chat(search_query, k=5)
    logger.info("[Chat] RAG retrieved %d chunks for query: %s", len(rag_chunks), search_query[:80])

    if not rag_chunks:
        logger.warning("[Chat] No RAG chunks retrieved — proceeding without context.")

    # --- Step 3: Load persistent conversation memory ---
    memory_enabled = bool(getattr(Config, "CHAT_MEMORY_ENABLED", True))
    conv_id = get_or_create_conversation_id(conversation_id, user_id)
    conversation = {}

    if memory_enabled:
        conversation = load_conversation(conv_id, user_id)
        logger.debug(
            "[Chat] Loaded conversation | id=%s | prior_messages=%d | topic=%s",
            conv_id,
            len(conversation.get("messages", [])),
            conversation.get("last_medical_topic", ""),
        )

    # --- Step 4: Build message list for Alchemyst ---
    if memory_enabled and conversation.get("messages"):
        # Use persistent memory as the message history
        messages = format_messages_for_alchemyst(conversation, query)
    else:
        # Fall back to legacy short-term history from caller
        messages = []
        for h in (history or [])[-_MAX_LEGACY_HISTORY:]:
            role = "user" if h.get("sender") == "user" else "assistant"
            content = str(h.get("text") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": query})

    # --- Step 5: Call Alchemyst (or Groq fallback) ---
    logger.info(
        "[Chat] Calling chatbot LLM | provider=%s | language=%s | messages=%d | rag_chunks=%d",
        getattr(Config, "CHATBOT_PROVIDER", "ALCHEMYST"), language,
        len(messages), len(rag_chunks),
    )

    llm_result = ask_chatbot_llm(
        messages=messages,
        rag_chunks=rag_chunks,
        language=language,
        metadata=metadata,
    )

    answer = str(llm_result.get("answer") or "").strip()
    follow_up_raw = llm_result.get("follow_up_questions") or []
    sources_used_ids = set(llm_result.get("sources_used") or [])

    # --- Step 6: Handle empty answer ---
    if not answer:
        logger.warning("[Chat] Empty answer from LLM — returning fallback.")
        answer = (
            "மன்னிக்கவும், பதில் உருவாக்க முடியவில்லை. மீண்டும் முயற்சிக்கவும்."
            if language == "ta-IN"
            else "I was unable to generate a response. Please try again."
        )

    # --- Step 7: Filter docs to sources actually used ---
    if sources_used_ids:
        used_docs = [doc for doc in rag_chunks if doc.get("chunk_id") in sources_used_ids]
    else:
        used_docs = rag_chunks  # fallback: return all retrieved

    # Hide docs if answer says "not available"
    if any(phrase in answer.lower() for phrase in ["not available", "do not have", "cannot find"]):
        used_docs = []

    # --- Step 8: Persist memory ---
    if memory_enabled:
        rag_chunk_ids = [doc.get("chunk_id", "") for doc in rag_chunks if doc.get("chunk_id")]
        topic = _extract_medical_topic(query)

        append_turn(conversation, query, answer, rag_chunk_ids=rag_chunk_ids)
        conversation["language"] = language
        if metadata:
            conversation["ai_context"].update(metadata)
        if topic:
            conversation["last_medical_topic"] = topic

        saved = save_conversation(
            conv_id,
            user_id,
            conversation.get("messages", []),
            rag_chunks_used=rag_chunk_ids,
            language=language,
            ai_context=conversation.get("ai_context", {}),
            last_medical_topic=conversation.get("last_medical_topic", ""),
        )
        logger.debug("[Chat] Conversation persisted | id=%s | saved=%s", conv_id, saved)

    # --- Step 9: Normalize follow-ups ---
    follow_up_questions = _normalize_follow_ups(follow_up_raw, language)

    logger.info(
        "[Chat] Response ready | answer_len=%d | docs=%d | follow_ups=%d",
        len(answer), len(used_docs), len(follow_up_questions),
    )

    return answer, used_docs, follow_up_questions


# ---------------------------------------------------------------------------
# Internal constant
# ---------------------------------------------------------------------------
_MAX_LEGACY_HISTORY = 8  # max legacy short-term history turns to include
