import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    load_dotenv = None


class Config:

    # ================================
    # FLASK
    # ================================
    SECRET_KEY = os.getenv("SECRET_KEY")
    FLASK_ENV = os.getenv("FLASK_ENV", "development")

    # ================================
    # MONGODB
    # ================================
    MONGO_URI = os.getenv("MONGO_URI")

    # ================================
    # LLM (GROQ)
    # ================================
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama3-8b-8192")

    # ================================
    # SUPABASE STORAGE
    # ================================
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY")        # anon/public key
    SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")  # service_role key
    SUPABASE_BILL_BUCKET = os.getenv("SUPABASE_BILL_BUCKET", "medical-bills")
    SUPABASE_LETTER_BUCKET = os.getenv("SUPABASE_LETTER_BUCKET", "letters")

    # ================================
    # OCR.SPACE
    # ================================
    OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "helloworld")

    # ================================
    # JWT
    # ================================
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
    JWT_EXPIRATION = int(os.getenv("JWT_EXPIRATION", 86400))

    # ================================
    # OTP SERVICE (MSG91)
    # ================================
    MSG91_API_KEY = os.getenv("MSG91_API_KEY")
    MSG91_TEMPLATE_ID = os.getenv("MSG91_TEMPLATE_ID")
    MSG91_SENDER_ID = os.getenv("MSG91_SENDER_ID")
    OTP_EXPIRY = int(os.getenv("OTP_EXPIRY", 300))

    # ================================
    # FILE UPLOAD SETTINGS
    # ================================
    UPLOAD_MAX_SIZE = int(os.getenv("UPLOAD_MAX_SIZE", 15 * 1024 * 1024))
    FILE_ALLOWED_EXTENSIONS = set(
        os.getenv("FILE_ALLOWED_EXTENSIONS", "pdf,png,jpg,jpeg").split(",")
    )

    # ================================
    # RAG / VECTOR STORE
    # ================================
    VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "data/vector_store/")
    ANNEXURE_PATH = os.getenv("ANNEXURE_PATH", "resources/annexures/annexure_I.pdf")
    ANNEXURE_IA_PATH = os.getenv("ANNEXURE_IA_PATH", "resources/annexures/annexure_IA.pdf")
    POPPLER_PATH = os.getenv("POPPLER_PATH", r"C:\poppler\Library\bin")
    VECTOR_AUTO_REBUILD = os.getenv("VECTOR_AUTO_REBUILD", "false").lower() == "true"
    CLAIM_PROCESSING_ASYNC = os.getenv("CLAIM_PROCESSING_ASYNC", "false").lower() == "true"
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # ================================
    # FRAUD WEIGHTS
    # ================================
    FRAUD_WEIGHTS = {
        "duplicate": int(os.getenv("FRAUD_WEIGHT_DUPLICATE", 80)),
        "similarity": int(os.getenv("FRAUD_WEIGHT_SIMILARITY", 60)),
        "repeated_claim": int(os.getenv("FRAUD_WEIGHT_REPEATED_CLAIM", 30)),
    }

    # ================================
    # STARTUP VALIDATION
    # ================================
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")

    @classmethod
    def validate(cls):
        """Validate critical environment variables on startup."""
        missing = []
        critical = {
            "SECRET_KEY": cls.SECRET_KEY,
            "MONGO_URI": cls.MONGO_URI,
            "GROQ_API_KEY": cls.GROQ_API_KEY,
            "SUPABASE_URL": cls.SUPABASE_URL,
            "SUPABASE_SECRET_KEY": cls.SUPABASE_SECRET_KEY,
            "OCR_SPACE_API_KEY": cls.OCR_SPACE_API_KEY,
            "ANNEXURE_PATH": cls.ANNEXURE_PATH,
        }
        for key, val in critical.items():
            if not val:
                missing.append(key)
        if missing:
            raise EnvironmentError(
                f"[MediCurance] STARTUP FAILED - Missing critical environment variables: {', '.join(missing)}"
            )

        return True
