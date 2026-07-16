from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, Set

try:
    from pydantic import Field  # type: ignore
    from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore
    _HAS_PYDANTIC_SETTINGS = True
except Exception:  # pragma: no cover - fallback for lean runtimes
    Field = None  # type: ignore
    BaseSettings = object  # type: ignore
    SettingsConfigDict = None  # type: ignore
    _HAS_PYDANTIC_SETTINGS = False


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class AppSettings(BaseSettings):  # type: ignore[misc]
    if _HAS_PYDANTIC_SETTINGS:
        secret_key: str = Field(default="fallback-secret-for-dev", alias="SECRET_KEY")
        flask_env: str = Field(default="development", alias="FLASK_ENV")
        mongo_uri: str = Field(..., alias="MONGO_URI")

        llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")
        groq_api_key: str = Field(..., alias="GROQ_API_KEY")
        groq_model: str = Field(default="llama3-8b-8192", alias="GROQ_MODEL")

        supabase_url: str = Field(..., alias="SUPABASE_URL")
        supabase_api_key: str = Field(default="", alias="SUPABASE_API_KEY")
        supabase_secret_key: str = Field(default="", alias="SUPABASE_SECRET_KEY")
        supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
        supabase_db_url: str = Field(default="", alias="SUPABASE_DB_URL")
        supabase_bill_bucket: str = Field(default="medical-bills", alias="SUPABASE_BILL_BUCKET")
        supabase_letter_bucket: str = Field(default="letters", alias="SUPABASE_LETTER_BUCKET")

        ocr_space_api_key: str = Field(default="helloworld", alias="OCR_SPACE_API_KEY")

        gnani_api_key_id: str = Field(default="", alias="GNANI_API_KEY_ID")
        gnani_api_key: str = Field(default="", alias="GNANI_API_KEY")
        gnani_base_url: str = Field(default="https://api.vachana.ai", alias="GNANI_BASE_URL")
        gnani_stt_url: str = Field(default="", alias="GNANI_STT_URL")
        gnani_tts_url: str = Field(default="", alias="GNANI_TTS_URL")
        gnani_tts_product: str = Field(default="Gnani Timbre v2.0", alias="GNANI_TTS_PRODUCT")
        gnani_tts_model: str = Field(default="vachana-voice-v3", alias="GNANI_TTS_MODEL")
        gnani_tts_voice_type: str = Field(default="Standard Voices", alias="GNANI_TTS_VOICE_TYPE")
        gnani_tts_voice: str = Field(default="Pranav", alias="GNANI_TTS_VOICE")
        gnani_language: str = Field(default="en-IN", alias="GNANI_LANGUAGE")
        gnani_voice: str = Field(default="Pranav", alias="GNANI_VOICE")
        gnani_audio_format: str = Field(default="wav", alias="GNANI_AUDIO_FORMAT")

        jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")
        jwt_expiration: int = Field(default=86400, alias="JWT_EXPIRATION")
        jwt_access_exp_minutes: int = Field(default=15, alias="JWT_ACCESS_EXP_MINUTES")
        jwt_refresh_exp_days: int = Field(default=14, alias="JWT_REFRESH_EXP_DAYS")
        jwt_cookie_secure: bool = Field(default=False, alias="JWT_COOKIE_SECURE")
        jwt_cookie_samesite: str = Field(default="Lax", alias="JWT_COOKIE_SAMESITE")
        jwt_cookie_name: str = Field(default="medicurance_access_token", alias="JWT_COOKIE_NAME")
        jwt_refresh_cookie_name: str = Field(default="medicurance_refresh_token", alias="JWT_REFRESH_COOKIE_NAME")
        jwt_issuer: str = Field(default="medicurance", alias="JWT_ISSUER")

        msg91_api_key: str = Field(default="", alias="MSG91_API_KEY")
        msg91_template_id: str = Field(default="", alias="MSG91_TEMPLATE_ID")
        msg91_sender_id: str = Field(default="", alias="MSG91_SENDER_ID")
        otp_expiry: int = Field(default=300, alias="OTP_EXPIRY")

        upload_max_size: int = Field(default=15 * 1024 * 1024, alias="UPLOAD_MAX_SIZE")
        file_allowed_extensions: str = Field(default="pdf,png,jpg,jpeg", alias="FILE_ALLOWED_EXTENSIONS")

        vector_db_path: str = Field(default="data/vector_store/", alias="VECTOR_DB_PATH")
        annexure_path: str = Field(default="resources/annexures/annexure_I.pdf", alias="ANNEXURE_PATH")
        annexure_ia_path: str = Field(default="", alias="ANNEXURE_IA_PATH")
        poppler_path: str = Field(default=r"C:\poppler\Library\bin", alias="POPPLER_PATH")
        vector_auto_rebuild: bool = Field(default=False, alias="VECTOR_AUTO_REBUILD")
        claim_processing_async: bool = Field(default=False, alias="CLAIM_PROCESSING_ASYNC")
        redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

        fraud_weight_duplicate: int = Field(default=80, alias="FRAUD_WEIGHT_DUPLICATE")
        fraud_weight_similarity: int = Field(default=60, alias="FRAUD_WEIGHT_SIMILARITY")
        fraud_weight_repeated_claim: int = Field(default=30, alias="FRAUD_WEIGHT_REPEATED_CLAIM")

        ratelimit_storage_uri: str = Field(default="memory://", alias="RATELIMIT_STORAGE_URI")
        cors_allowed_origins: str = Field(default="http://localhost:5000,http://127.0.0.1:5000", alias="CORS_ALLOWED_ORIGINS")
        api_docs_enabled: bool = Field(default=True, alias="API_DOCS_ENABLED")
        api_version: str = Field(default="v1", alias="API_VERSION")
        request_timeout_seconds: int = Field(default=30, alias="REQUEST_TIMEOUT_SECONDS")
        embedding_cache_ttl_seconds: int = Field(default=3600, alias="EMBEDDING_CACHE_TTL_SECONDS")
        hospital_cache_ttl_seconds: int = Field(default=900, alias="HOSPITAL_CACHE_TTL_SECONDS")
        claim_cache_ttl_seconds: int = Field(default=300, alias="CLAIM_CACHE_TTL_SECONDS")
        enable_background_jobs: bool = Field(default=False, alias="ENABLE_BACKGROUND_JOBS")
        groq_api_url: str = Field(default="https://api.groq.com/openai/v1/chat/completions", alias="GROQ_API_URL")
        nhis_start_url: str = Field(default="https://tnnhis2018.in/TNEMPLOYEE/TNPolInfo.aspx", alias="NHIS_START_URL")
        nhis_allowed_netlocs: str = Field(default="tnnhis2018.in,www.tnnhis2018.in", alias="NHIS_ALLOWED_NETLOCS")
        nhis_allowed_prefixes: str = Field(default="/TNEMPLOYEE/,/TNEMPLOYEE/TNPolInfo.aspx", alias="NHIS_ALLOWED_PREFIXES")
        ocr_space_url: str = Field(default="https://api.ocr.space/parse/image", alias="OCR_SPACE_URL")
        msg91_api_url: str = Field(default="https://control.msg91.com/api/v5/sms", alias="MSG91_API_URL")

        model_config = SettingsConfigDict(env_file=".env", extra="ignore")

        def model_post_init(self, __context) -> None:
            if not self.jwt_secret_key:
                self.jwt_secret_key = self.secret_key
    else:
        def __init__(self):
            self.secret_key = os.getenv("SECRET_KEY", "fallback-secret-for-dev")
            self.flask_env = os.getenv("FLASK_ENV", "development")
            self.mongo_uri = os.getenv("MONGO_URI", "")
            self.llm_provider = os.getenv("LLM_PROVIDER", "groq")
            self.groq_api_key = os.getenv("GROQ_API_KEY", "")
            self.groq_model = os.getenv("GROQ_MODEL", "llama3-8b-8192")
            self.supabase_url = os.getenv("SUPABASE_URL", "")
            self.supabase_api_key = os.getenv("SUPABASE_API_KEY", "")
            self.supabase_secret_key = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
            self.supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or self.supabase_secret_key
            self.supabase_db_url = os.getenv("SUPABASE_DB_URL", "")
            self.supabase_bill_bucket = os.getenv("SUPABASE_BILL_BUCKET", "medical-bills")
            self.supabase_letter_bucket = os.getenv("SUPABASE_LETTER_BUCKET", "letters")
            self.ocr_space_api_key = os.getenv("OCR_SPACE_API_KEY", "helloworld")
            self.gnani_api_key_id = os.getenv("GNANI_API_KEY_ID", "")
            self.gnani_api_key = os.getenv("GNANI_API_KEY", self.gnani_api_key_id)
            self.gnani_base_url = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai")
            self.gnani_stt_url = os.getenv("GNANI_STT_URL", "").strip()
            self.gnani_tts_url = os.getenv("GNANI_TTS_URL", "").strip()
            self.gnani_tts_product = os.getenv("GNANI_TTS_PRODUCT", "Gnani Timbre v2.0")
            self.gnani_tts_model = os.getenv("GNANI_TTS_MODEL", "vachana-voice-v3")
            self.gnani_tts_voice_type = os.getenv("GNANI_TTS_VOICE_TYPE", "Standard Voices")
            self.gnani_tts_voice = os.getenv("GNANI_TTS_VOICE", "Pranav")
            self.gnani_language = os.getenv("GNANI_LANGUAGE", "en-IN")
            self.gnani_voice = os.getenv("GNANI_VOICE", "Pranav")
            self.gnani_audio_format = os.getenv("GNANI_AUDIO_FORMAT", "wav")
            self.jwt_secret_key = os.getenv("JWT_SECRET_KEY") or self.secret_key
            self.jwt_expiration = _env_int("JWT_EXPIRATION", 86400)
            self.jwt_access_exp_minutes = _env_int("JWT_ACCESS_EXP_MINUTES", 15)
            self.jwt_refresh_exp_days = _env_int("JWT_REFRESH_EXP_DAYS", 14)
            self.jwt_cookie_secure = _env_bool("JWT_COOKIE_SECURE", self.flask_env != "development")
            self.jwt_cookie_samesite = os.getenv("JWT_COOKIE_SAMESITE", "Lax")
            self.jwt_cookie_name = os.getenv("JWT_COOKIE_NAME", "medicurance_access_token")
            self.jwt_refresh_cookie_name = os.getenv("JWT_REFRESH_COOKIE_NAME", "medicurance_refresh_token")
            self.jwt_issuer = os.getenv("JWT_ISSUER", "medicurance")
            self.msg91_api_key = os.getenv("MSG91_API_KEY", "")
            self.msg91_template_id = os.getenv("MSG91_TEMPLATE_ID", "")
            self.msg91_sender_id = os.getenv("MSG91_SENDER_ID", "")
            self.otp_expiry = _env_int("OTP_EXPIRY", 300)
            self.upload_max_size = _env_int("UPLOAD_MAX_SIZE", 15 * 1024 * 1024)
            self.file_allowed_extensions = os.getenv("FILE_ALLOWED_EXTENSIONS", "pdf,png,jpg,jpeg")
            self.vector_db_path = os.getenv("VECTOR_DB_PATH", "data/vector_store/")
            self.annexure_path = os.getenv("ANNEXURE_PATH", "resources/annexures/annexure_I.pdf")
            self.annexure_ia_path = os.getenv("ANNEXURE_IA_PATH", "")
            self.poppler_path = os.getenv("POPPLER_PATH", r"C:\poppler\Library\bin")
            self.vector_auto_rebuild = _env_bool("VECTOR_AUTO_REBUILD", False)
            self.claim_processing_async = _env_bool("CLAIM_PROCESSING_ASYNC", False)
            self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            self.fraud_weight_duplicate = _env_int("FRAUD_WEIGHT_DUPLICATE", 80)
            self.fraud_weight_similarity = _env_int("FRAUD_WEIGHT_SIMILARITY", 60)
            self.fraud_weight_repeated_claim = _env_int("FRAUD_WEIGHT_REPEATED_CLAIM", 30)
            self.ratelimit_storage_uri = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
            self.cors_allowed_origins = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000")
            self.api_docs_enabled = _env_bool("API_DOCS_ENABLED", True)
            self.api_version = os.getenv("API_VERSION", "v1")
            self.request_timeout_seconds = _env_int("REQUEST_TIMEOUT_SECONDS", 30)
            self.embedding_cache_ttl_seconds = _env_int("EMBEDDING_CACHE_TTL_SECONDS", 3600)
            self.hospital_cache_ttl_seconds = _env_int("HOSPITAL_CACHE_TTL_SECONDS", 900)
            self.claim_cache_ttl_seconds = _env_int("CLAIM_CACHE_TTL_SECONDS", 300)
            self.enable_background_jobs = _env_bool("ENABLE_BACKGROUND_JOBS", False)
            self.groq_api_url = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")
            self.nhis_start_url = os.getenv("NHIS_START_URL", "https://tnnhis2018.in/TNEMPLOYEE/TNPolInfo.aspx")
            self.nhis_allowed_netlocs = os.getenv("NHIS_ALLOWED_NETLOCS", "tnnhis2018.in,www.tnnhis2018.in")
            self.nhis_allowed_prefixes = os.getenv("NHIS_ALLOWED_PREFIXES", "/TNEMPLOYEE/,/TNEMPLOYEE/TNPolInfo.aspx")
            self.ocr_space_url = os.getenv("OCR_SPACE_URL", "https://api.ocr.space/parse/image")
            self.msg91_api_url = os.getenv("MSG91_API_URL", "https://control.msg91.com/api/v5/sms")

    @property
    def allowed_extensions_set(self) -> Set[str]:
        return {item.strip().lower() for item in str(self.file_allowed_extensions).split(",") if item.strip()}

    @property
    def nhis_allowed_netlocs_set(self) -> Set[str]:
        return {item.strip() for item in str(self.nhis_allowed_netlocs).split(",") if item.strip()}

    @property
    def nhis_allowed_prefixes_tuple(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in str(self.nhis_allowed_prefixes).split(",") if item.strip())

    @property
    def fraud_weights(self) -> Dict[str, int]:
        return {
            "duplicate": int(self.fraud_weight_duplicate),
            "similarity": int(self.fraud_weight_similarity),
            "repeated_claim": int(self.fraud_weight_repeated_claim),
        }

    def validate(self) -> bool:
        missing = []
        critical = {
            "SECRET_KEY": self.secret_key,
            "MONGO_URI": self.mongo_uri,
            "GROQ_API_KEY": self.groq_api_key,
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SECRET_KEY": self.supabase_secret_key or self.supabase_service_role_key,
            "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key or self.supabase_secret_key,
            "OCR_SPACE_API_KEY": self.ocr_space_api_key,
            "ANNEXURE_PATH": self.annexure_path,
        }
        for key, val in critical.items():
            if not val:
                missing.append(key)
        if missing:
            raise EnvironmentError(
                f"[MediCurance] STARTUP FAILED - Missing critical environment variables: {', '.join(missing)}"
            )
        if not (self.gnani_api_key_id or self.gnani_api_key):
            logger_msg = "[MediCurance] GNANI_API_KEY_ID/GNANI_API_KEY not set; voice features will be disabled."
            try:
                import logging
                logging.getLogger(__name__).warning(logger_msg)
            except Exception:
                pass
        return True


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
