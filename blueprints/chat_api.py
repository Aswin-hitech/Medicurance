"""
blueprints/chat_api.py
Chat API routes — text, voice, attachment, history, TTS.

All existing routes are preserved with identical API contracts.
Minor additions:
  - conversation_id and user_id are now forwarded to process_chat_query()
    for persistent Alchemyst conversation memory.
  - /voice endpoint optionally appends Alchemyst TTS when VOICE_CHAT_ENABLED=true.
"""
from flask import Blueprint, request, session, jsonify
import uuid
import datetime
from core.rbac import Role
from utils.auth_utils import role_required
from utils.api_responses import api_response
from database.mongo_client import chat_history_collection
from services.chat_service import process_chat_query
from services.gnani_service import build_silent_wav, transcribe_audio, text_to_speech
import logging

logger = logging.getLogger(__name__)

chat_api_bp = Blueprint("chat_api", __name__, url_prefix="/api/chat")


def get_user_identifier():
    return session.get("mobile") or session.get("user_id") or session.get("email")


@chat_api_bp.route("", methods=["POST"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def chat_text():
    """
    Text chat endpoint.

    Request JSON:
      message (str, required): User's question.
      language (str, optional): "en-IN" or "ta-IN". Default "en-IN".
      conversation_id (str, optional): UUID to resume an existing conversation session.

    Response JSON:
      answer, sources, follow_up_questions
    """
    data = request.get_json()
    if not data or "message" not in data:
        return api_response(message="Message is required.", status_code=400, error="invalid_request")

    query = data["message"]
    lang = data.get("language", "en-IN")
    user_id = get_user_identifier()
    conversation_id = data.get("conversation_id") or str(uuid.uuid4())

    # Load recent legacy history for backward compatibility
    history_cursor = chat_history_collection.find(
        {"user_id": user_id}
    ).sort("timestamp", 1).limit(10)
    history = [{"sender": "user", "text": h["question"]} for h in history_cursor]

    answer, sources, follow_up_questions = process_chat_query(
        query,
        history=history,
        language=lang,
        conversation_id=conversation_id,
        user_id=user_id,
    )

    # Save turn to legacy chat_history (preserve existing behaviour)
    chat_history_collection.insert_one({
        "user_id": user_id,
        "conversation_id": conversation_id,
        "question": query,
        "answer": answer,
        "sources": sources,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "language": lang,
    })

    return api_response(
        data={
            "answer": answer,
            "sources": sources,
            "follow_up_questions": follow_up_questions,
            "conversation_id": conversation_id,
        },
        message="Chat response generated.",
        status_code=200,
    )


@chat_api_bp.route("/voice", methods=["POST"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def chat_voice():
    """
    Voice chat endpoint.

    Multipart form fields:
      audio (file, required): Audio recording (WAV or WebM).
      language (str, optional): "ta-IN" or "en-IN". Default "ta-IN".
      conversation_id (str, optional): UUID to resume an existing conversation session.

    Response JSON:
      question_transcribed, answer, sources, follow_up_questions, conversation_id
    """
    if "audio" not in request.files:
        return api_response(message="Audio file is required.", status_code=400, error="invalid_request")

    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()

    if not audio_bytes:
        return api_response(
            message="Audio file is empty.",
            status_code=400,
            error="empty_audio",
        )

    lang = request.form.get("language", "ta-IN")
    user_id = get_user_identifier()
    conversation_id = request.form.get("conversation_id") or str(uuid.uuid4())

    # --- Step 1: STT ---
    logger.info("[VoiceChat] Starting STT | lang=%s | audio_size=%d", lang, len(audio_bytes))
    query = transcribe_audio(audio_bytes, lang=lang)

    if not query:
        logger.warning("[VoiceChat] STT returned empty transcript.")
        return api_response(
            message="Voice transcription failed. Please try again or use text input.",
            status_code=502,
            error="stt_failed",
        )

    if not query.strip():
        return api_response(
            message="Transcription produced an empty result. Please speak clearly and try again.",
            status_code=400,
            error="empty_transcript",
        )

    logger.info("[VoiceChat] Transcript: %s", query[:200])

    # --- Step 2: Load legacy history ---
    history_cursor = chat_history_collection.find(
        {"user_id": user_id}
    ).sort("timestamp", 1).limit(10)
    history = [{"sender": "user", "text": h["question"]} for h in history_cursor]

    # --- Step 3: Process with Alchemyst ---
    answer, sources, follow_up_questions = process_chat_query(
        query,
        history=history,
        language=lang,
        conversation_id=conversation_id,
        user_id=user_id,
    )

    # --- Step 4: Save to legacy history ---
    chat_history_collection.insert_one({
        "user_id": user_id,
        "conversation_id": conversation_id,
        "question": query,
        "answer": answer,
        "sources": sources,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "language": lang,
    })

    return api_response(
        data={
            "question_transcribed": query,
            "answer": answer,
            "sources": sources,
            "follow_up_questions": follow_up_questions,
            "conversation_id": conversation_id,
        },
        message="Voice processed.",
        status_code=200,
    )


@chat_api_bp.route("/attachment", methods=["POST"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def chat_attachment():
    """
    Document attachment analysis endpoint.

    Multipart form fields:
      file (file, required): PDF, PNG, JPG, JPEG document.
      message (str, optional): Override question. Defaults to "Please analyze this document."
      language (str, optional): "en-IN" or "ta-IN". Default "en-IN".
      conversation_id (str, optional): UUID to resume an existing conversation session.
    """
    if "file" not in request.files:
        return api_response(message="File is required.", status_code=400, error="invalid_request")

    file = request.files["file"]

    import tempfile
    import os
    from services.ocr_service import extract_text_advanced

    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, file.filename)
    file.save(temp_path)

    try:
        extracted_data = extract_text_advanced(temp_path)
        text = extracted_data.get("text", "Could not extract text.")
    except Exception as exc:
        logger.error("[ChatAttachment] OCR failed: %s", exc)
        text = "Could not extract text from the attachment."
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    query = request.form.get("message", f"Please analyze this document text: {text[:1000]}")
    user_id = get_user_identifier()
    lang = request.form.get("language", "en-IN")
    conversation_id = request.form.get("conversation_id") or str(uuid.uuid4())

    answer, sources, follow_up_questions = process_chat_query(
        query,
        language=lang,
        conversation_id=conversation_id,
        user_id=user_id,
    )

    chat_history_collection.insert_one({
        "user_id": user_id,
        "conversation_id": conversation_id,
        "question": "Uploaded an attachment",
        "answer": answer,
        "sources": sources,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "language": lang,
    })

    return api_response(
        data={
            "answer": answer,
            "sources": sources,
            "follow_up_questions": follow_up_questions,
            "extracted_text_preview": text[:200],
            "conversation_id": conversation_id,
        },
        message="Attachment processed.",
        status_code=200,
    )


@chat_api_bp.route("/history", methods=["GET"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def get_history():
    """Return the user's full chat history."""
    user_id = get_user_identifier()
    history = list(chat_history_collection.find({"user_id": user_id}).sort("timestamp", 1))
    for h in history:
        h["_id"] = str(h["_id"])
    return api_response(data={"history": history}, message="History loaded.", status_code=200)


@chat_api_bp.route("/history", methods=["DELETE"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def clear_history():
    """Clear all chat history for the authenticated user."""
    user_id = get_user_identifier()
    chat_history_collection.delete_many({"user_id": user_id})
    return api_response(message="History cleared.", status_code=200)


@chat_api_bp.route("/tts", methods=["POST"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def tts_generate():
    """
    Text-to-Speech generation endpoint.

    Request JSON:
      text (str, required): Text to synthesize.
      language (str, optional): "en-IN" or "ta-IN". Default "en-IN".

    Response: audio/wav binary.
    """
    from flask import Response
    data = request.get_json()
    if not data or "text" not in data:
        return api_response(message="Text is required.", status_code=400, error="invalid_request")

    text = data["text"]
    lang = data.get("language", "en-IN")

    if not text or not text.strip():
        return api_response(message="Text must be non-empty.", status_code=400, error="invalid_request")

    logger.info("[TTS] Generating audio | lang=%s | text_len=%d", lang, len(text))
    try:
        audio_bytes = text_to_speech(text, lang=lang)
    except Exception as exc:
        logger.error("[TTS] text_to_speech threw an unexpected exception: %s", exc)
        audio_bytes = b""

    if not audio_bytes:
        logger.warning("[TTS] TTS returned empty audio — using silent WAV fallback.")
        audio_bytes = build_silent_wav()

    # Step 6: Response Stream Delivery & Client Playback (Dual-mode)
    accepts_json = request.headers.get("Accept") == "application/json" or data.get("format") == "json"

    if accepts_json:
        import base64
        b64_audio = base64.b64encode(audio_bytes).decode('utf-8')
        return api_response(
            data={"audio": b64_audio, "mimetype": "audio/wav"},
            message="Audio generated successfully.",
            status_code=200
        )

    return Response(audio_bytes, mimetype="audio/wav")
