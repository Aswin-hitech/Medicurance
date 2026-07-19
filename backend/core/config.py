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
    SUPABASE_BILL_BUCKET = settings.supabase_bill_bucket
    SUPABASE_LETTER_BUCKET = settings.supabase_letter_bucket
    OCR_SPACE_API_KEY = settings.ocr_space_api_key
    JWT_SECRET_KEY = settings.jwt_secret_key
    JWT_EXPIRATION = settings.jwt_expiration
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

    @classmethod
    def validate(cls):
        return settings.validate()

