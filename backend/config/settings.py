from core.settings import AppSettings, get_settings


settings = get_settings()


class Config:
    SECRET_KEY = settings.secret_key
    FLASK_ENV = settings.flask_env
    MONGO_URI = settings.mongo_uri
    LLM_PROVIDER = settings.llm_provider
    GROQ_API_KEY = settings.groq_api_key
    GROQ_MODEL = settings.groq_model
    SUPABASE_URL = settings.supabase_url
    SUPABASE_API_KEY = settings.supabase_api_key
    SUPABASE_SECRET_KEY = settings.supabase_secret_key
    SUPABASE_SERVICE_ROLE_KEY = settings.supabase_secret_key
    SUPABASE_DB_URL = settings.supabase_db_url
    SUPABASE_BILL_BUCKET = settings.supabase_bill_bucket
    SUPABASE_LETTER_BUCKET = settings.supabase_letter_bucket
    OCR_SPACE_API_KEY = settings.ocr_space_api_key
    GNANI_API_KEY_ID = getattr(settings, "gnani_api_key_id", "") or getattr(settings, "gnani_api_key", "")
    GNANI_API_KEY = getattr(settings, "gnani_api_key", "") or getattr(settings, "gnani_api_key_id", "")
    GNANI_BASE_URL = getattr(settings, "gnani_base_url", "https://api.vachana.ai")
    GNANI_STT_URL = getattr(settings, "gnani_stt_url", "")
    OCR_SPACE_URL = getattr(settings, "ocr_space_url", "https://api.ocr.space/parse/image")
    GNANI_TTS_URL = getattr(settings, "gnani_tts_url", "")
    GNANI_TTS_PRODUCT = getattr(settings, "gnani_tts_product", "Gnani Timbre v2.0")
    GNANI_TTS_MODEL = getattr(settings, "gnani_tts_model", "vachana-voice-v3")
    GNANI_TTS_VOICE_TYPE = getattr(settings, "gnani_tts_voice_type", "Standard Voices")
    GNANI_TTS_VOICE = getattr(settings, "gnani_tts_voice", "Pranav")
    GNANI_LANGUAGE = getattr(settings, "gnani_language", "en-IN")
    GNANI_VOICE = getattr(settings, "gnani_voice", "Pranav")
    GNANI_AUDIO_FORMAT = getattr(settings, "gnani_audio_format", "wav")
    GNANI_TIMEOUT = getattr(settings, "gnani_timeout", 60)
    GNANI_DEFAULT_LANGUAGE = getattr(settings, "gnani_default_language", "en-IN")
    GNANI_TAMIL_VOICE = getattr(settings, "gnani_tamil_voice", "Kaveri")
    GNANI_ENGLISH_VOICE = getattr(settings, "gnani_english_voice", "Pranav")
    # Alchemyst AI
    ALCHEMYST_AI_API_KEY = getattr(settings, "alchemyst_ai_api_key", "")
    ALCHEMYST_API_KEY = getattr(settings, "alchemyst_api_key", "")
    ALCHEMYST_BASE_URL = getattr(settings, "alchemyst_base_url", "https://platform-backend.getalchemystai.com")
    ALCHEMYST_API_BASE = getattr(settings, "alchemyst_api_base", "https://platform-backend.getalchemystai.com/api/v1/proxy")
    ALCHEMYST_CHAT_URL = getattr(settings, "alchemyst_chat_url", "https://platform-backend.getalchemystai.com/api/v1/chat")
    ALCHEMYST_MODEL = getattr(settings, "alchemyst_model", "alchemyst-c-01")
    ALCHEMYST_TIMEOUT = getattr(settings, "alchemyst_timeout", 60)
    # Chat feature flags
    CHAT_MEMORY_ENABLED = getattr(settings, "chat_memory_enabled", True)
    VOICE_CHAT_ENABLED = getattr(settings, "voice_chat_enabled", True)
    CHATBOT_PROVIDER = getattr(settings, "chatbot_provider", "ALCHEMYST")
    JWT_SECRET_KEY = settings.jwt_secret_key
    JWT_EXPIRATION = settings.jwt_expiration
    JWT_ACCESS_EXP_MINUTES = settings.jwt_access_exp_minutes
    JWT_REFRESH_EXP_DAYS = settings.jwt_refresh_exp_days
    JWT_COOKIE_SECURE = settings.jwt_cookie_secure
    JWT_COOKIE_SAMESITE = settings.jwt_cookie_samesite
    JWT_COOKIE_NAME = settings.jwt_cookie_name
    JWT_REFRESH_COOKIE_NAME = settings.jwt_refresh_cookie_name
    JWT_ISSUER = settings.jwt_issuer
    TWILIO_ACCOUNT_SID = getattr(settings, "twilio_account_sid", "")
    TWILIO_AUTH_TOKEN = getattr(settings, "twilio_auth_token", "")
    TWILIO_PHONE_NUMBER = getattr(settings, "twilio_phone_number", "")
    OTP_EXPIRY = settings.otp_expiry
    UPLOAD_MAX_SIZE = settings.upload_max_size
    FILE_ALLOWED_EXTENSIONS = settings.allowed_extensions_set
    VECTOR_DB_PATH = settings.vector_db_path
    ANNEXURE_PATH = settings.annexure_path
    ANNEXURE_IA_PATH = settings.annexure_ia_path
    POPPLER_PATH = settings.poppler_path
    VECTOR_AUTO_REBUILD = settings.vector_auto_rebuild
    CLAIM_PROCESSING_ASYNC = settings.claim_processing_async
    REDIS_URL = settings.redis_url
    FRAUD_WEIGHTS = settings.fraud_weights
    RATELIMIT_STORAGE_URI = settings.ratelimit_storage_uri
    CORS_ALLOWED_ORIGINS = settings.cors_allowed_origins
    API_DOCS_ENABLED = settings.api_docs_enabled
    API_VERSION = settings.api_version
    REQUEST_TIMEOUT_SECONDS = settings.request_timeout_seconds
    EMBEDDING_CACHE_TTL_SECONDS = settings.embedding_cache_ttl_seconds
    HOSPITAL_CACHE_TTL_SECONDS = settings.hospital_cache_ttl_seconds
    CLAIM_CACHE_TTL_SECONDS = settings.claim_cache_ttl_seconds
    ENABLE_BACKGROUND_JOBS = settings.enable_background_jobs
    GROQ_API_URL = settings.groq_api_url
    NHIS_START_URL = settings.nhis_start_url
    NHIS_ALLOWED_NETLOCS = settings.nhis_allowed_netlocs_set
    NHIS_ALLOWED_PREFIXES = settings.nhis_allowed_prefixes_tuple
    OCR_SPACE_URL = settings.ocr_space_url


    @classmethod
    def validate(cls):
        return settings.validate()
