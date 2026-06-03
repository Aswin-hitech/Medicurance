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
            "https://api.groq.com/openai/v1/chat/completions",
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
