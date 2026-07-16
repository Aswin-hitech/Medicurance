import json
import logging
from typing import List, Dict, Any, Tuple
from services.rag_service import retrieve_rules
from services.llm_service import ask_llm

logger = logging.getLogger(__name__)


_SMALL_TALK_PATTERNS = [
    "hi", "hello", "hey", "namaste", "thanks", "thank you", "thx",
    "வணக்கம்", "ஹாய்", "நன்றி", "hello there", "good morning", "good afternoon", "good evening"
]


def _is_small_talk(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    if any(pattern in text for pattern in _SMALL_TALK_PATTERNS):
        return len(text.split()) <= 6 or text in {"hi", "hello", "hey", "thanks", "thank you", "thankyou", "வணக்கம்", "நன்றி"}
    return False


def _small_talk_response(language: str, query: str) -> str:
    text = (query or "").strip().lower()
    if language == "ta-IN":
        if any(word in text for word in ["நன்றி", "thanks", "thank you", "thankyou"]):
            return "நன்றி! நான் உதவ தயாராக இருக்கிறேன். உங்கள் மருத்துவக் கோரிக்கை, தகுதி, அல்லது தேவையான ஆவணங்கள் பற்றி கேளுங்கள்."
        return "வணக்கம்! நான் உங்கள் AI பென்ஷன் உதவியாளர். உங்கள் கோரிக்கை, தகுதி, அல்லது அடுத்த படிகள் பற்றி நான் உதவ முடியும்."
    if any(word in text for word in ["நன்றி", "thanks", "thank you", "thankyou"]):
        return "You're very welcome. I'm here to help with eligibility, documents, claims, or next steps."
    return "Hello! I’m your AI Pension Assistant. I can help with eligibility, required documents, claims, and next steps."


def _default_follow_ups(language: str) -> List[str]:
    if language == "ta-IN":
        return [
            "தகுதி உள்ளதா?",
            "தேவையான ஆவணங்கள் என்ன?",
            "அடுத்த படி என்ன?",
        ]
    return [
        "Check eligibility",
        "Required docs",
        "Next steps",
    ]


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

def process_chat_query(query: str, history: List[Dict[str, str]] = None, language: str = "en-IN") -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """
    Process a chat query using RAG and LLM.
    Returns: (answer_text, retrieved_sources)
    """
    if not query.strip():
        return "Please provide a valid question.", [], _default_follow_ups(language)

    if _is_small_talk(query):
        return _small_talk_response(language, query), [], _default_follow_ups(language)

    search_query = query
    if language == "ta-IN":
        try:
            translation_prompt = f"Translate the following Tamil text to English for a search query. Return ONLY the English translation without any extra words or markdown:\n\n{query}"
            english_query = ask_llm(translation_prompt, json_mode=False)
            if english_query and not english_query.startswith("{"):  # check it's not a fallback json
                search_query = english_query.strip()
                logger.info(f"Translated Tamil query to English for RAG: {search_query}")
        except Exception as e:
            logger.error(f"Failed to translate Tamil query: {e}")

    # 1. Retrieve knowledge using the (translated) search query
    docs = retrieve_rules(search_query, k=5)
    
    if not docs:
        return "The requested information is not available in the indexed Government knowledge base.", [], _default_follow_ups(language)

    # 2. Build context
    context_parts = []
    for doc in docs:
        context_parts.append(f"Source: {doc['source_document']} (ID: {doc['chunk_id']})\nRule: {doc['matched_rule']}")
    
    context = "\n\n".join(context_parts)

    # 3. Build Prompt
    history_str = ""
    if history:
        history_str = "Chat History:\n" + "\n".join([f"{msg['sender']}: {msg['text']}" for msg in history[-4:]]) + "\n\n"

    lang_instruction = "IMPORTANT: You MUST write your 'answer' in Tamil language." if language == "ta-IN" else "IMPORTANT: You MUST write your 'answer' in English language."

    prompt = f"""You are the AI Pension Assistant for the Government of Tamil Nadu (Medicurance).
Your goal is to answer the user's question STRICTLY based on the provided Government Scheme Rules.
Do NOT hallucinate. Do NOT use outside knowledge. If the answer is not in the rules, say "The requested information is not available in the indexed Government knowledge base."
{lang_instruction}
Be warm, friendly, and conversational when the user greets you or says thanks.
Always make the response interactive and helpful. Add 2 to 4 short follow-up suggestions the user can tap next.
If the user's language is Tamil, keep follow-up suggestions in Tamil. If English, keep them in English.

GOVERNMENT SCHEME RULES:
{context}

{history_str}
USER QUESTION: {query}

Return ONLY valid JSON with NO markdown and NO explanation:
{{
  "answer": "<your detailed answer using markdown>",
  "sources_used": ["<list of chunk_ids from the rules that you actually used to answer>"],
  "follow_up_questions": ["<short clickable follow-up suggestion>"]
}}"""

    # 4. Ask LLM
    try:
        raw_response = ask_llm(prompt, json_mode=True)
        # Parse response
        # The llm_service's ask_llm might return markdown-wrapped JSON or plain JSON
        import re
        raw = re.sub(r"^```(?:json)?", "", raw_response, flags=re.MULTILINE).strip()
        raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()
        
        result = json.loads(raw)
        answer = result.get("answer", "I could not generate an answer.")
        sources_used_ids = set(result.get("sources_used", []))
        follow_up_questions = _normalize_follow_ups(result.get("follow_up_questions", []), language)
        
        # Filter docs to only those used
        used_docs = [doc for doc in docs if doc['chunk_id'] in sources_used_ids]
        if not used_docs:
            used_docs = docs  # Fallback: if LLM failed to specify, return all retrieved docs

        if "not available" in answer.lower() or "do not have" in answer.lower():
            used_docs = []

        return answer, used_docs, follow_up_questions
    except Exception as exc:
        logger.error(f"[Chat Service] Failed to process query: {exc}")
        return "An error occurred while generating the response.", [], _default_follow_ups(language)
