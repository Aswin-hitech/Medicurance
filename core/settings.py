from __future__ import annotations

from functools import lru_cache
from typing import Dict, Set

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    secret_key: str = Field(default="fallback-secret-for-dev", alias="SECRET_KEY")
    flask_env: str = Field(default="development", alias="FLASK_ENV")

    mongo_uri: str = Field(..., alias="MONGO_URI")

    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")
    groq_api_key: str = Field(..., alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama3-8b-8192", alias="GROQ_MODEL")

    supabase_url: str = Field(..., alias="SUPABASE_URL")
    supabase_api_key: str = Field(default="", alias="SUPABASE_API_KEY")
    supabase_secret_key: str = Field(..., alias="SUPABASE_SECRET_KEY")
    supabase_bill_bucket: str = Field(default="medical-bills", alias="SUPABASE_BILL_BUCKET")
    supabase_letter_bucket: str = Field(default="letters", alias="SUPABASE_LETTER_BUCKET")

    ocr_space_api_key: str = Field(default="helloworld", alias="OCR_SPACE_API_KEY")

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
    annexure_ia_path: str = Field(default="resources/annexures/annexure_IA.pdf", alias="ANNEXURE_IA_PATH")
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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def model_post_init(self, __context) -> None:
        if not self.jwt_secret_key:
            self.jwt_secret_key = self.secret_key

    @property
    def allowed_extensions_set(self) -> Set[str]:
        return {item.strip().lower() for item in self.file_allowed_extensions.split(",") if item.strip()}

    @property
    def fraud_weights(self) -> Dict[str, int]:
        return {
            "duplicate": self.fraud_weight_duplicate,
            "similarity": self.fraud_weight_similarity,
            "repeated_claim": self.fraud_weight_repeated_claim,
        }

    def validate(self) -> bool:
        missing = []
        critical = {
            "SECRET_KEY": self.secret_key,
            "MONGO_URI": self.mongo_uri,
            "GROQ_API_KEY": self.groq_api_key,
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SECRET_KEY": self.supabase_secret_key,
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
        return True


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
