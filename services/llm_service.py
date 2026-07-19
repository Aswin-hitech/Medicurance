import json
import logging

from config.settings import Config

logger = logging.getLogger(__name__)


def _fallback_response(reason):
    return json.dumps({
        "eligibility": "Unknown",
        "confidence": 0.0,
        "risk_level": "High",
        "fraud_score": 0.5,
        "hospital_verified": False,
        "reasoning": f"LLM unavailable: {reason}",
        "recommended_action": "Review",
        "fraud_flags": [f"LLM unavailable: {reason}"],
        "missing_documents": [],
        "amount_analysis": {"claimed": 0, "expected_range": "N/A", "status": "anomalous"},
    })


def ask_llm(prompt, json_mode=False):
    if not getattr(Config, "GROQ_API_KEY", ""):
        logger.error("[LLM] Missing GROQ_API_KEY. Aborting request.")
        return _fallback_response("Missing GROQ_API_KEY")

    try:
        import requests
    except Exception as exc:
        logger.error(f"[LLM] Requests library unavailable: {exc}")
        return _fallback_response("requests unavailable")

    headers = {
        "Authorization": f"Bearer {Config.GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": Config.GROQ_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    if json_mode:
        data["response_format"] = {"type": "json_object"}

    logger.debug(
        "[LLM] Groq request payload: %s",
        json.dumps(
            {
                "model": data["model"],
                "messages": data["messages"],
                **({"response_format": data["response_format"]} if "response_format" in data else {}),
            },
            ensure_ascii=False
        )[:4000]
    )

    response = None
    try:
        response = requests.post(
            Config.GROQ_API_URL,
            headers=headers,
            json=data,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices", [])
        if not choices:
            logger.warning("[LLM] Empty response payload from Groq.")
            return _fallback_response("empty response")

        message = choices[0].get("message", {})
        content = message.get("content")
        if not content:
            logger.warning("[LLM] Groq response missing content.")
            return _fallback_response("missing content")

        return content
    except requests.RequestException as exc:
        status_code = getattr(response, "status_code", None)
        response_text = ""
        if response is not None:
            response_text = getattr(response, "text", "") or ""
        logger.error(
            "[LLM] Request failed | status=%s | body=%s | error=%s",
            status_code,
            response_text[:1000],
            exc,
        )
        reason = f"{status_code or 'no-status'}: {response_text[:120] or str(exc)[:120]}"
        return _fallback_response(reason)
    except (ValueError, KeyError, TypeError) as exc:
        logger.error(f"[LLM] Malformed Groq response: {exc}")
        if response is not None:
            logger.error(
                "[LLM] Response payload | status=%s | body=%s",
                getattr(response, "status_code", None),
                getattr(response, "text", "")[:1000],
            )
        return _fallback_response(str(exc)[:120])


# ---------------------------------------------------------------------------
# Chatbot LLM router — routes to Alchemyst (or Groq fallback)
# DO NOT use this function in the claim processing pipeline.
# Use ask_llm() for all claim/rag validation tasks.
# ---------------------------------------------------------------------------
def _groq_chat_fallback(
    messages: "list[dict]",
    rag_chunks: "list[dict]",
    language: str,
) -> "dict":
    """
    Emergency Groq fallback for the chatbot when Alchemyst is unavailable.
    Builds a simple single-turn prompt from the last user message + context.
    Returns a dict compatible with AlchemystResponse fields.
    """
    import re as _re

    history_lines = []
    for m in messages:
        role = str(m.get("role", "")).upper()
        content = str(m.get("content", "")).strip()
        if content:
            history_lines.append(f"{role}: {content}")
    history_text = "\n".join(history_lines)

    context = "\n\n".join(
        f"Source: {c.get('source_document', '')}\nRule: {c.get('matched_rule') or c.get('text', '')}"
        for c in (rag_chunks or [])[:5]
    )
    lang_note = "Respond in Tamil." if language == "ta-IN" else "Respond in English."
    prompt = (
        f"You are a medical AI assistant for Tamil Nadu Government's NHIS/CMCHIS scheme.\n"
        f"{lang_note}\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"CONVERSATION HISTORY:\n{history_text}\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{\"answer\": \"<your answer>\", \"follow_up_questions\": [\"...\", \"...\"], \"sources_used\": []}}'
    )
    raw = ask_llm(prompt, json_mode=True)
    # Parse
    raw = _re.sub(r"^```(?:json)?", "", raw, flags=_re.MULTILINE).strip()
    raw = _re.sub(r"```$", "", raw, flags=_re.MULTILINE).strip()
    try:
        import json as _json
        data = _json.loads(raw)
        return {
            "answer": data.get("answer", ""),
            "follow_up_questions": data.get("follow_up_questions", []),
            "sources_used": data.get("sources_used", []),
            "success": True,
            "error": "",
        }
    except Exception:
        return {
            "answer": raw or "An error occurred generating a response.",
            "follow_up_questions": [],
            "sources_used": [],
            "success": bool(raw),
            "error": "JSON parse failed on Groq fallback",
        }


def ask_chatbot_llm(
    messages: "list[dict]",
    rag_chunks: "list[dict] | None" = None,
    language: str = "en-IN",
    metadata: "dict | None" = None,
) -> "dict":
    """
    Primary entry point for the AI chatbot reasoning layer.

    Routes to Alchemyst AI when CHATBOT_PROVIDER=ALCHEMYST (default).
    Falls back to Groq if Alchemyst is unavailable or misconfigured.

    Args:
        messages:   OpenAI-format message list [{role, content}].
        rag_chunks: Retrieved annexure chunks from RAG retrieval.
        language:   "en-IN" or "ta-IN".
        metadata:   Optional extra context (claim_id, trust_score, etc.).

    Returns:
        Dict with keys: answer, follow_up_questions, sources_used, success, error.
        Never raises.
    """
    provider = str(getattr(Config, "CHATBOT_PROVIDER", "ALCHEMYST") or "ALCHEMYST").strip().upper()

    if provider == "ALCHEMYST":
        try:
            from services.alchemyst_service import ask_alchemyst, AlchemystResponse
            result: AlchemystResponse = ask_alchemyst(
                messages=messages,
                rag_chunks=rag_chunks or [],
                language=language,
                metadata=metadata,
            )
            if result.success and result.answer:
                logger.info(
                    "[Chatbot] Alchemyst responded | latency_ms=%.1f | tokens=%s",
                    result.latency_ms,
                    result.token_usage.get("total_tokens", "?"),
                )
                return {
                    "answer": result.answer,
                    "follow_up_questions": result.follow_up_questions,
                    "sources_used": result.sources_used,
                    "success": True,
                    "error": "",
                }
            else:
                logger.warning(
                    "[Chatbot] Alchemyst call failed (%s) — falling back to Groq.",
                    result.error,
                )
        except Exception as exc:
            logger.error("[Chatbot] Alchemyst import/call error: %s — falling back to Groq.", exc)

    # Groq fallback
    logger.info("[Chatbot] Using Groq fallback for chatbot response.")
    return _groq_chat_fallback(messages, rag_chunks or [], language)

