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
    data = request.get_json()
    if not data or "message" not in data:
        return api_response(message="Message is required.", status_code=400, error="invalid_request")
    
    query = data["message"]
    lang = data.get("language", "en-IN")
    user_id = get_user_identifier()
    
    # Load recent history
    history_cursor = chat_history_collection.find({"user_id": user_id}).sort("timestamp", 1).limit(10)
    history = [{"sender": "user", "text": h["question"]} for h in history_cursor]
    
    answer, sources, follow_up_questions = process_chat_query(query, history=history, language=lang)
    
    # Save to history
    chat_history_collection.insert_one({
        "user_id": user_id,
        "conversation_id": data.get("conversation_id", str(uuid.uuid4())),
        "question": query,
        "answer": answer,
        "sources": sources,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "language": lang
    })
    
    return api_response(data={"answer": answer, "sources": sources, "follow_up_questions": follow_up_questions}, message="Chat response generated.", status_code=200)

@chat_api_bp.route("/voice", methods=["POST"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def chat_voice():
    if "audio" not in request.files:
        return api_response(message="Audio file is required.", status_code=400, error="invalid_request")
    
    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    
    lang = request.form.get("language", "ta-IN")
    # 1. STT
    query = transcribe_audio(audio_bytes, lang=lang)
    if not query:
        return api_response(
            message="Voice transcription failed. Please try again.",
            status_code=502,
            error="stt_failed",
        )

    user_id = get_user_identifier()
    
    # Load recent history
    history_cursor = chat_history_collection.find({"user_id": user_id}).sort("timestamp", 1).limit(10)
    history = [{"sender": "user", "text": h["question"]} for h in history_cursor]
    
    # 2. Process query
    answer, sources, follow_up_questions = process_chat_query(query, history=history, language=lang)
    
    # 3. Text output returned to frontend.

    
    chat_history_collection.insert_one({
        "user_id": user_id,
        "conversation_id": request.form.get("conversation_id", str(uuid.uuid4())),
        "question": query,
        "answer": answer,
        "sources": sources,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "language": lang
    })
    
    return api_response(data={"question_transcribed": query, "answer": answer, "sources": sources, "follow_up_questions": follow_up_questions}, message="Voice processed.", status_code=200)

@chat_api_bp.route("/attachment", methods=["POST"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def chat_attachment():
    if "file" not in request.files:
        return api_response(message="File is required.", status_code=400, error="invalid_request")
    
    file = request.files["file"]
    
    # Save file to temp location
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
        logger.error(f"OCR failed: {exc}")
        text = "Could not extract text from the attachment."
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    query = request.form.get("message", f"Please analyze this document text: {text[:1000]}")
    user_id = get_user_identifier()
    
    lang = request.form.get("language", "en-IN")
    answer, sources, follow_up_questions = process_chat_query(query, language=lang)
    
    chat_history_collection.insert_one({
        "user_id": user_id,
        "conversation_id": request.form.get("conversation_id", str(uuid.uuid4())),
        "question": "Uploaded an attachment",
        "answer": answer,
        "sources": sources,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "language": lang
    })
    
    return api_response(data={"answer": answer, "sources": sources, "follow_up_questions": follow_up_questions, "extracted_text_preview": text[:200]}, message="Attachment processed.", status_code=200)

@chat_api_bp.route("/history", methods=["GET"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def get_history():
    user_id = get_user_identifier()
    history = list(chat_history_collection.find({"user_id": user_id}).sort("timestamp", 1))
    for h in history:
        h["_id"] = str(h["_id"])
    return api_response(data={"history": history}, message="History loaded.", status_code=200)

@chat_api_bp.route("/history", methods=["DELETE"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def clear_history():
    user_id = get_user_identifier()
    chat_history_collection.delete_many({"user_id": user_id})
    return api_response(message="History cleared.", status_code=200)

@chat_api_bp.route("/tts", methods=["POST"])
@role_required([Role.CITIZEN.value, Role.OFFICER.value, Role.ADMIN.value])
def tts_generate():
    from flask import Response
    data = request.get_json()
    if not data or "text" not in data:
        return api_response(message="Text is required.", status_code=400, error="invalid_request")
    
    text = data["text"]
    lang = data.get("language", "en-IN")
    
    audio_bytes = text_to_speech(text, lang=lang)
    if not audio_bytes:
        audio_bytes = build_silent_wav()
        
    return Response(audio_bytes, mimetype="audio/wav")
