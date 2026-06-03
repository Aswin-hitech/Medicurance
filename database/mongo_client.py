import logging
from datetime import datetime, timezone

from pymongo import MongoClient

from config.settings import Config

logger = logging.getLogger(__name__)


class _EmptyCursor(list):
    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self


class _WriteResult:
    def __init__(self, inserted_id=None, matched_count=0, modified_count=0, deleted_count=0):
        self.inserted_id = inserted_id
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.deleted_count = deleted_count


class _UnavailableCollection:
    def __init__(self, name: str):
        self.name = name

    def find(self, *args, **kwargs):
        return _EmptyCursor()

    def find_one(self, *args, **kwargs):
        return None

    def count_documents(self, *args, **kwargs):
        return 0

    def estimated_document_count(self, *args, **kwargs):
        return 0

    def aggregate(self, *args, **kwargs):
        return iter(())

    def insert_one(self, *args, **kwargs):
        logger.warning("[MongoDB] insert_one skipped; collection %s unavailable.", self.name)
        return _WriteResult()

    def insert_many(self, *args, **kwargs):
        logger.warning("[MongoDB] insert_many skipped; collection %s unavailable.", self.name)
        return _WriteResult()

    def update_one(self, *args, **kwargs):
        logger.warning("[MongoDB] update_one skipped; collection %s unavailable.", self.name)
        return _WriteResult()

    def delete_one(self, *args, **kwargs):
        logger.warning("[MongoDB] delete_one skipped; collection %s unavailable.", self.name)
        return _WriteResult()

    def create_index(self, *args, **kwargs):
        logger.warning("[MongoDB] create_index skipped; collection %s unavailable.", self.name)
        return None

    def __getattr__(self, item):
        def _noop(*args, **kwargs):
            logger.warning("[MongoDB] %s.%s skipped; collection unavailable.", self.name, item)
            return None

        return _noop


class _UnavailableDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, _UnavailableCollection(name))

    def command(self, *args, **kwargs):
        raise RuntimeError("MongoDB unavailable")


def _safe_create_index(collection, keys, **kwargs):
    if not MONGO_AVAILABLE:
        return None
    try:
        return collection.create_index(keys, **kwargs)
    except Exception as exc:
        logger.warning(
            "[MongoDB] Index creation skipped for %s | keys=%s | error=%s",
            getattr(collection, "name", "unknown"),
            keys,
            exc,
        )
        return None

try:
    client = MongoClient(
        Config.MONGO_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000,
        retryWrites=True,
    )
    client.admin.command("ping")
    logger.info("[MongoDB] Connection validated successfully.")
except Exception as exc:
    logger.warning("[MongoDB] Connection validation failed: %s", exc)
    client = None

if client is not None:
    db = client["medicurance"]
else:
    db = _UnavailableDB()

MONGO_AVAILABLE = client is not None

# Collections
users_collection = db["users"]
claims_collection = db["claims"]
documents_collection = db["documents"]
hospitals_collection = db["hospitals"]
otp_collection = db["otp"]
govt_collection = db["govtlist"]
officers_collection = db["government_officers"]
admins_collection = db["admins"]
audit_logs_collection = db["audit_logs"]
claim_logs_collection = db["claim_logs"]
notifications_collection = db["notifications"]

# Advanced Indexing
if MONGO_AVAILABLE:
    _safe_create_index(users_collection, "mobile", unique=True)
    _safe_create_index(users_collection, "employee_id")
    _safe_create_index(users_collection, "email", sparse=True)

    _safe_create_index(claims_collection, "claim_id", unique=True)
    _safe_create_index(claims_collection, "mobile")
    _safe_create_index(claims_collection, "status")
    _safe_create_index(claims_collection, "created_at")
    _safe_create_index(claims_collection, "hospital")
    _safe_create_index(claims_collection, "image_hash")
    _safe_create_index(claims_collection, "duplicate_hash")

    _safe_create_index(hospitals_collection, "name", unique=True)

    _safe_create_index(govt_collection, "phone_number", unique=True)
    _safe_create_index(govt_collection, "employee_id", unique=True)
    _safe_create_index(govt_collection, "email", unique=True)
    _safe_create_index(govt_collection, "aadhaar_number", unique=True, sparse=True)

    _safe_create_index(officers_collection, "officer_id", unique=True)
    _safe_create_index(officers_collection, "employee_id", unique=True, sparse=True)
    _safe_create_index(officers_collection, "email", unique=True)
    _safe_create_index(officers_collection, "phone", unique=True)
    _safe_create_index(officers_collection, "phone_number", unique=True, sparse=True)
    _safe_create_index(officers_collection, "mobile")

    _safe_create_index(admins_collection, "email", unique=True)
    _safe_create_index(audit_logs_collection, "timestamp")
    _safe_create_index(audit_logs_collection, "action")
    _safe_create_index(audit_logs_collection, "actor")
    _safe_create_index(claim_logs_collection, "claim_id")
    _safe_create_index(claim_logs_collection, "timestamp")
    _safe_create_index(notifications_collection, "claim_id")
    _safe_create_index(notifications_collection, "created_at")

    if Config.FLASK_ENV == "development" and admins_collection.count_documents({}) == 0:
        import bcrypt

        hashed = bcrypt.hashpw("admin123".encode("utf-8"), bcrypt.gensalt())
        admins_collection.insert_one({
            "email": "admin@medicurance.gov.in",
            "password": hashed,
            "name": "Super Admin",
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("[MongoDB] Development admin seeded.")
    elif admins_collection.count_documents({}) == 0:
        logger.warning("[MongoDB] Admin seed skipped outside development mode.")

    _safe_create_index(otp_collection, "timestamp", expireAfterSeconds=300)
