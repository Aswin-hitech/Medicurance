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
    JWT_SECRET_KEY = settings.jwt_secret_key
    JWT_EXPIRATION = settings.jwt_expiration
    JWT_ACCESS_EXP_MINUTES = settings.jwt_access_exp_minutes
    JWT_REFRESH_EXP_DAYS = settings.jwt_refresh_exp_days
    JWT_COOKIE_SECURE = settings.jwt_cookie_secure
    JWT_COOKIE_SAMESITE = settings.jwt_cookie_samesite
    JWT_COOKIE_NAME = settings.jwt_cookie_name
    JWT_REFRESH_COOKIE_NAME = settings.jwt_refresh_cookie_name
    JWT_ISSUER = settings.jwt_issuer
    MSG91_API_KEY = settings.msg91_api_key
    MSG91_TEMPLATE_ID = settings.msg91_template_id
    MSG91_SENDER_ID = settings.msg91_sender_id
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

    @classmethod
    def validate(cls):
        return settings.validate()
