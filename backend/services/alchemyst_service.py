"""
services/alchemyst_service.py
Alchemyst AI conversational reasoning engine — official SDK client.

Uses the official `alchemystai` Python SDK (pip install alchemystai).
SDK docs: https://platform-backend.getalchemystai.com

SDK API surface used here:
  client.v1.context.search(...)          — semantic context retrieval
  client.v1.context.add(...)             — ingest documents into context store
  client.v1.context.memory.add(...)      — persist conversation turns
  client.v1.context.memory.update(...)   — update conversation session memory
  client.v1.context.memory.delete(...)   — remove conversation memory
  POST /api/v1/proxy (via httpx)         — OpenAI-compatible chat completions

Responsibilities:
  - Send multi-turn conversation messages with RAG context to Alchemyst AI.
  - Support English and Tamil responses.
  - Retry with exponential backoff on transient errors.
  - Structured logging of request, response, token usage, and latency.
  - Graceful fallback: never raise unhandled exceptions to callers.

Environment variable:
  ALCHEMYST_AI_API_KEY  — your Alchemyst AI API key (preferred)
  ALCHEMYST_API_KEY     — legacy fallback key name
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from config.settings import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MAX_RETRIES = 3
_BACKOFF_FACTOR = 2.0           # 2s, 4s, 8s
_MAX_CONTEXT_CHARS = 6000       # guard against oversized prompts
_MAX_HISTORY_TURNS = 10         # how many prior messages to include
_CHAT_TIMEOUT = 60              # seconds for chat completion call


# ---------------------------------------------------------------------------
# Response dataclass
# ---------------------------------------------------------------------------
@dataclass
class AlchemystResponse:
    answer: str = ""
    follow_up_questions: List[str] = field(default_factory=list)
    sources_used: List[str] = field(default_factory=list)
    token_usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    success: bool = True
    error: str = ""


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def _api_key() -> str:
    """
    Prefer ALCHEMYST_AI_API_KEY (official SDK env var name).
    Fall back to legacy ALCHEMYST_API_KEY.
    """
    key = (
        str(getattr(Config, "ALCHEMYST_AI_API_KEY", "") or "").strip()
        or str(getattr(Config, "ALCHEMYST_API_KEY", "") or "").strip()
    )
    return key


def _base_url() -> str:
    configured = str(getattr(Config, "ALCHEMYST_BASE_URL", "") or "").strip().rstrip("/")
    return configured or "https://platform-backend.getalchemystai.com"


def _proxy_url() -> str:
    """OpenAI-compatible drop-in proxy: ALCHEMYST_API_BASE."""
    configured = str(getattr(Config, "ALCHEMYST_API_BASE", "") or "").strip().rstrip("/")
    return configured or f"{_base_url()}/api/v1/proxy"


def _chat_url() -> str:
    """Direct REST chat endpoint: ALCHEMYST_CHAT_URL."""
    configured = str(getattr(Config, "ALCHEMYST_CHAT_URL", "") or "").strip().rstrip("/")
    return configured or f"{_base_url()}/api/v1/chat"


def _model() -> str:
    return str(getattr(Config, "ALCHEMYST_MODEL", "alchemyst-c-01") or "alchemyst-c-01").strip()


def _timeout() -> int:
    try:
        return int(getattr(Config, "ALCHEMYST_TIMEOUT", _CHAT_TIMEOUT))
    except (TypeError, ValueError):
        return _CHAT_TIMEOUT


def _get_sdk_client():
    """Lazily import and initialise the official AlchemystAI SDK client."""
    try:
        from alchemyst_ai import AlchemystAI  # type: ignore
        return AlchemystAI(
            api_key=_api_key(),
            base_url=_base_url(),
            max_retries=_MAX_RETRIES,
            timeout=float(_timeout()),
        )
    except ImportError:
        logger.error(
            "[Alchemyst] alchemystai SDK not installed. Run: pip install alchemystai"
        )
        return None


# ---------------------------------------------------------------------------
# Prompt / context builders
# ---------------------------------------------------------------------------
def _build_system_prompt(language: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    meta = metadata or {}
    lang_instruction = (
        "IMPORTANT: You MUST respond entirely in Tamil language."
        if language == "ta-IN"
        else "IMPORTANT: You MUST respond in English."
    )

    claim_context = ""
    if meta.get("claim_id"):
        claim_context = f"\nCurrent claim context — Claim ID: {meta['claim_id']}"
        if meta.get("trust_score") is not None:
            claim_context += f" | Trust Score: {meta['trust_score']}"
        if meta.get("eligibility"):
            claim_context += f" | Eligibility: {meta['eligibility']}"
        if meta.get("recommendation"):
            claim_context += f" | Recommendation: {meta['recommendation']}"

    return f"""You are the AI Medical Assistant for Medicurance — Tamil Nadu Government's medical reimbursement system (NHIS/CMCHIS).

You expertly explain:
- Government Annexure Rules & Eligibility Criteria
- Medical Bills, Procedures, and Treatments
- Disease diagnoses, medical terminology, and treatment plans
- AI Trust Scores, Fraud Detection, and OCR Results
- Claim Eligibility, Officer Decisions, and Recommendations
- Required Documents and Next Steps for government employees
- Scheme benefits and reimbursement limits

You have access to retrieved government scheme rules as CONTEXT below. Use this context to ground your answers.
Do NOT hallucinate. If the answer is not in the context, say so clearly and helpfully.

Always respond in a warm, professional, and conversational tone.
Generate 2–4 short, actionable follow-up questions the user can tap next.
{lang_instruction}
{claim_context}

Return ONLY valid JSON (no markdown fences, no explanation outside JSON):
{{
  "answer": "<your detailed markdown-formatted answer>",
  "follow_up_questions": ["<short question 1>", "<short question 2>", "<short question 3>"],
  "sources_used": ["<chunk_id or source reference used>"]
}}"""


def _build_context_block(rag_chunks: List[Dict[str, Any]]) -> str:
    """Format RAG chunks into a context string injected into the system prompt."""
    if not rag_chunks:
        return ""
    parts = []
    for chunk in rag_chunks[:10]:
        source = chunk.get("source_document", "Unknown Source")
        text = (chunk.get("matched_rule") or chunk.get("text") or "").strip()
        chunk_id = chunk.get("chunk_id", "")
        if text:
            parts.append(f"[{chunk_id}] {source}:\n{text}")
    combined = "\n\n---\n\n".join(parts)
    return combined[:_MAX_CONTEXT_CHARS]


# ---------------------------------------------------------------------------
# SDK — context search (replaces manual RAG retrieval if desired)
# ---------------------------------------------------------------------------
def search_context(
    query: str,
    similarity_threshold: float = 0.7,
    minimum_similarity_threshold: float = 0.4,
    scope: str = "internal",
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search Alchemyst context store using the official SDK.

    Args:
        query:                       Natural language query.
        similarity_threshold:        High-confidence match threshold (0–1).
        minimum_similarity_threshold: Minimum score to include a result.
        scope:                       "internal" or "external".
        user_id:                     Optional user scoping.

    Returns:
        List of result dicts from the context store, or [] on failure.
    """
    client = _get_sdk_client()
    if client is None:
        return []

    try:
        logger.info(
            "[Alchemyst SDK] context.search | query=%s | threshold=%.2f | scope=%s",
            query[:100], similarity_threshold, scope,
        )
        response = client.v1.context.search(
            query=query,
            similarity_threshold=similarity_threshold,
            minimum_similarity_threshold=minimum_similarity_threshold,
            scope=scope,
            **({"user_id": user_id} if user_id else {}),
        )
        # SDK returns a ContextSearchResponse — convert to plain list of dicts
        raw = response if isinstance(response, list) else getattr(response, "results", []) or []
        results = [r if isinstance(r, dict) else r.model_dump() for r in raw]
        logger.info("[Alchemyst SDK] context.search returned %d results.", len(results))
        return results
    except Exception as exc:
        logger.error("[Alchemyst SDK] context.search error: %s", exc, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# SDK — ingest document into context store
# ---------------------------------------------------------------------------
def add_context_document(
    content: str,
    source: str,
    context_type: str = "resource",
    scope: str = "internal",
    file_name: Optional[str] = None,
    group_name: Optional[List[str]] = None,
) -> bool:
    """
    Ingest a document into the Alchemyst context store.

    Args:
        content:      The document text.
        source:       Source identifier (e.g. "annexure-i", "medicurance-docs").
        context_type: "resource" | "conversation" | "instruction".
        scope:        "internal" | "external".
        file_name:    Optional filename metadata.
        group_name:   Optional namespace tags for the document.

    Returns:
        True on success, False on failure.
    """
    client = _get_sdk_client()
    if client is None:
        return False

    doc: Dict[str, Any] = {"content": content}
    meta: Dict[str, Any] = {}
    if file_name:
        meta["file_name"] = file_name
        meta["file_type"] = file_name.rsplit(".", 1)[-1] if "." in file_name else "text"
    if group_name:
        meta["group_name"] = group_name
    if meta:
        doc["metadata"] = meta

    try:
        logger.info(
            "[Alchemyst SDK] context.add | source=%s | type=%s | scope=%s | size=%d",
            source, context_type, scope, len(content),
        )
        client.v1.context.add(
            documents=[doc],
            source=source,
            context_type=context_type,
            scope=scope,
        )
        logger.info("[Alchemyst SDK] context.add success.")
        return True
    except Exception as exc:
        logger.error("[Alchemyst SDK] context.add error: %s", exc, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# SDK — conversation memory
# ---------------------------------------------------------------------------
def save_conversation_memory(
    session_id: str,
    turns: List[Dict[str, str]],
    user_id: Optional[str] = None,
) -> bool:
    """
    Persist conversation turns to Alchemyst memory store.

    Args:
        session_id: Unique session / conversation UUID.
        turns:      List of {"role": "user"|"assistant", "content": "..."} dicts.
        user_id:    Optional user identifier for scoping.

    Returns:
        True on success, False on failure.
    """
    client = _get_sdk_client()
    if client is None:
        return False

    contents = [
        {"role": t.get("role", "user"), "content": t.get("content", "")}
        for t in turns
        if t.get("content")
    ]
    if not contents:
        return False

    meta: Dict[str, Any] = {}
    if user_id:
        meta["user_id"] = user_id

    try:
        logger.info(
            "[Alchemyst SDK] context.memory.add | session=%s | turns=%d",
            session_id, len(contents),
        )
        client.v1.context.memory.add(
            session_id=session_id,
            contents=contents,
            **({"metadata": meta} if meta else {}),
        )
        logger.info("[Alchemyst SDK] memory.add success.")
        return True
    except Exception as exc:
        logger.error("[Alchemyst SDK] context.memory.add error: %s", exc, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def _parse_response(raw: str, language: str) -> AlchemystResponse:
    """
    Parse Alchemyst chat response. Attempts JSON, falls back to plain text.
    """
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE).strip()
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

    # Direct JSON parse
    try:
        data = json.loads(raw)
        answer = str(data.get("answer") or data.get("content") or "").strip()
        follow_ups = [str(q).strip() for q in (data.get("follow_up_questions") or []) if str(q).strip()]
        sources = [str(s).strip() for s in (data.get("sources_used") or []) if str(s).strip()]
        if answer:
            return AlchemystResponse(
                answer=answer,
                follow_up_questions=follow_ups,
                sources_used=sources,
                success=True,
            )
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("[Alchemyst] Direct JSON parse failed: %s", exc)

    # Embedded JSON search
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            answer = str(data.get("answer") or data.get("content") or "").strip()
            if answer:
                return AlchemystResponse(
                    answer=answer,
                    follow_up_questions=[str(q).strip() for q in (data.get("follow_up_questions") or []) if str(q).strip()],
                    sources_used=[str(s).strip() for s in (data.get("sources_used") or []) if str(s).strip()],
                    success=True,
                )
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("[Alchemyst] Embedded JSON parse failed: %s", exc)

    # Plain text fallback
    if raw:
        logger.warning("[Alchemyst] Response is not JSON — using raw text as answer.")
        return AlchemystResponse(answer=raw, success=True)

    return AlchemystResponse(
        answer="I was unable to generate a response. Please try again.",
        success=False,
        error="Empty or unparseable Alchemyst response",
    )


def _default_follow_ups(language: str) -> List[str]:
    if language == "ta-IN":
        return ["தகுதி உள்ளதா?", "தேவையான ஆவணங்கள் என்ன?", "அடுத்த படி என்ன?"]
    return ["Check eligibility", "Required documents", "Next steps", "Explain my trust score"]


# ---------------------------------------------------------------------------
# Chat completion — dual-endpoint strategy
# ---------------------------------------------------------------------------
def _chat_via_proxy(
    full_messages: List[Dict[str, str]],
    api_key: str,
    timeout: int,
) -> tuple[str, Dict[str, int]]:
    """
    Try Alchemyst chat endpoints in order:
      1. ALCHEMYST_API_BASE  (OpenAI-compatible proxy: /api/v1/proxy)
      2. ALCHEMYST_CHAT_URL  (Direct REST: /api/v1/chat)

    Uses absolute URLs from Config — no path construction from base URL.
    Returns (raw_content, token_usage) or raises RuntimeError on failure.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Build payloads for each endpoint style
    proxy_payload = {
        "model": _model(),
        "messages": full_messages,
        "temperature": 0.3,
        "max_tokens": 1500,
    }

    last_user_content = ""
    for m in reversed(full_messages):
        if m.get("role") == "user":
            last_user_content = m.get("content", "")
            break

    chat_payload = {
        "model": _model(),
        "message": last_user_content,
        "messages": full_messages,
        "stream": False,
    }

    # Endpoints tried in order: proxy (OpenAI-compatible), then direct chat
    endpoints = [
        (_proxy_url(), proxy_payload, "proxy"),
        (_chat_url(), chat_payload, "chat"),
    ]

    last_error = ""
    for url, payload, label in endpoints:
        logger.info("[Alchemyst] POST %s (%s) | messages=%d", url, label, len(full_messages))

        try:
            with httpx.Client(timeout=timeout) as http:
                resp = http.post(url, headers=headers, json=payload)

            logger.info(
                "[Alchemyst] %s response | status=%d | size=%d",
                label, resp.status_code, len(resp.content),
            )

            # 404/405 = wrong path — skip to next endpoint
            if resp.status_code in (404, 405):
                logger.warning("[Alchemyst] %d on %s — trying next endpoint.", resp.status_code, url)
                last_error = f"HTTP {resp.status_code} on {url}"
                continue

            # Auth errors — don't retry, surface immediately
            if resp.status_code in (401, 403):
                err = resp.text[:400]
                logger.error("[Alchemyst] Auth error | status=%d | body=%s", resp.status_code, err)
                raise RuntimeError(f"HTTP {resp.status_code}: {err[:120]}")

            if not resp.is_success:
                err = resp.text[:400]
                logger.error("[Alchemyst] %s error | status=%d | body=%s", label, resp.status_code, err)
                last_error = f"HTTP {resp.status_code}: {err[:120]}"
                continue

            body = resp.json()

            # Extract content from multiple possible response shapes
            raw_content = ""
            # OpenAI-compatible: choices[0].message.content
            choices = body.get("choices") or []
            if choices:
                raw_content = (choices[0].get("message") or {}).get("content") or ""
            # Alchemyst-native response fields
            if not raw_content:
                raw_content = (
                    str(body.get("response") or "").strip()
                    or str(body.get("final_response") or "").strip()
                    or str(body.get("answer") or "").strip()
                )

            if not raw_content:
                logger.warning("[Alchemyst] Empty content from %s. Keys: %s", url, list(body.keys())[:15])
                last_error = f"Empty content from {url}"
                continue

            usage = body.get("usage") or {}
            token_usage = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
            logger.info("[Alchemyst] SUCCESS via %s | tokens=%s", label, usage.get("total_tokens", "?"))
            return raw_content, token_usage

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("[Alchemyst] Network error on %s: %s", url, exc)
            last_error = f"Network error on {label}: {str(exc)[:80]}"
            continue

    raise RuntimeError(last_error or "All Alchemyst endpoints failed")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def ask_alchemyst(
    messages: List[Dict[str, str]],
    rag_chunks: Optional[List[Dict[str, Any]]] = None,
    language: str = "en-IN",
    metadata: Optional[Dict[str, Any]] = None,
) -> AlchemystResponse:
    """
    Send a multi-turn conversation with optional RAG context to Alchemyst AI.

    Workflow:
      1. Build system prompt with injected RAG context.
      2. POST to Context Proxy API (/api/v1/proxy) — OpenAI-compatible with
         Alchemyst's persistent memory enhancement layer.
      3. Retry with exponential backoff on transient failures.
      4. Parse and return structured AlchemystResponse.

    Args:
        messages:   OpenAI-format message list [{\"role\": \"user\", \"content\": \"...\"}].
        rag_chunks: Retrieved annexure chunks from retrieve_rules().
        language:   \"en-IN\" or \"ta-IN\".
        metadata:   Optional dict with claim_id, trust_score, eligibility, recommendation.

    Returns:
        AlchemystResponse dataclass. Never raises.
    """
    api_key = _api_key()
    if not api_key:
        logger.warning("[Alchemyst] No API key configured (ALCHEMYST_AI_API_KEY / ALCHEMYST_API_KEY).")
        return AlchemystResponse(
            answer="",
            success=False,
            error="ALCHEMYST_AI_API_KEY not configured",
        )

    if not messages:
        return AlchemystResponse(
            answer="Please provide a question.",
            success=False,
            error="Empty messages list",
        )

    # Build system prompt with injected RAG context
    context_block = _build_context_block(rag_chunks or [])
    system_prompt = _build_system_prompt(language, metadata)
    if context_block:
        system_prompt += f"\n\n=== GOVERNMENT SCHEME CONTEXT ===\n{context_block}\n=== END CONTEXT ==="

    # Assemble full message list: system + trimmed history + current user message
    full_messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    prior = [m for m in messages[:-1] if m.get("role") in ("user", "assistant")]
    if len(prior) > _MAX_HISTORY_TURNS * 2:
        prior = prior[-(_MAX_HISTORY_TURNS * 2):]
    full_messages.extend(prior)
    last_msg = messages[-1]
    full_messages.append({"role": last_msg.get("role", "user"), "content": last_msg.get("content", "")})

    base = _base_url()
    timeout = _timeout()

    logger.info(
        "[Alchemyst] Request | language=%s | messages=%d | rag_chunks=%d | proxy=%s | chat=%s",
        language, len(full_messages), len(rag_chunks or []),
        _proxy_url(), _chat_url(),
    )

    start_ts = time.monotonic()

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            raw_content, token_usage = _chat_via_proxy(full_messages, api_key, timeout)
            latency_ms = (time.monotonic() - start_ts) * 1000

            if not raw_content:
                logger.warning("[Alchemyst] Empty content on attempt %d/%d.", attempt, _MAX_RETRIES)
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_FACTOR ** (attempt - 1))
                    continue
                break

            logger.info(
                "[Alchemyst] SUCCESS | attempt=%d | tokens=%s | latency_ms=%.1f",
                attempt, token_usage.get("total_tokens", "?"), latency_ms,
            )
            logger.debug("[Alchemyst] Raw content preview: %s", raw_content[:300])

            result = _parse_response(raw_content, language)
            result.latency_ms = latency_ms
            result.token_usage = token_usage

            if not result.follow_up_questions:
                result.follow_up_questions = _default_follow_ups(language)

            return result

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            latency_ms = (time.monotonic() - start_ts) * 1000
            logger.warning(
                "[Alchemyst] Network error on attempt %d/%d: %s",
                attempt, _MAX_RETRIES, exc,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF_FACTOR ** (attempt - 1))
            else:
                return AlchemystResponse(
                    success=False,
                    error=f"Network error after {_MAX_RETRIES} attempts: {str(exc)[:120]}",
                    latency_ms=latency_ms,
                )

        except RuntimeError as exc:
            # HTTP error from proxy (e.g. 401, 403, 400) — don't retry auth errors
            latency_ms = (time.monotonic() - start_ts) * 1000
            err_str = str(exc)
            is_auth_error = any(code in err_str for code in ("401", "403"))
            logger.error("[Alchemyst] HTTP error: %s", exc)
            if is_auth_error or attempt >= _MAX_RETRIES:
                return AlchemystResponse(
                    success=False,
                    error=err_str[:200],
                    latency_ms=latency_ms,
                )
            time.sleep(_BACKOFF_FACTOR ** (attempt - 1))

        except Exception as exc:
            latency_ms = (time.monotonic() - start_ts) * 1000
            logger.error("[Alchemyst] Unexpected error: %s", exc, exc_info=True)
            return AlchemystResponse(
                success=False,
                error=f"Unexpected error: {str(exc)[:120]}",
                latency_ms=latency_ms,
            )

    latency_ms = (time.monotonic() - start_ts) * 1000
    return AlchemystResponse(
        success=False,
        error=f"All {_MAX_RETRIES} attempts failed with empty response.",
        latency_ms=latency_ms,
    )
