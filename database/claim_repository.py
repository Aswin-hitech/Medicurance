from database.mongo_client import claims_collection
from utils.status_utils import normalize_claim_status


def create_claim(data):
    claims_collection.insert_one(data)


def get_claims_by_user(mobile, skip=0, limit=0, sort_field="created_at", sort_order=-1):
    cursor = claims_collection.find({"mobile": mobile}).sort(sort_field, sort_order)
    if skip:
        cursor = cursor.skip(int(skip))
    if limit:
        cursor = cursor.limit(int(limit))
    claims = list(cursor)
    for claim in claims:
        claim["status"] = normalize_claim_status(claim.get("status"))
    return claims


def get_all_claims(skip=0, limit=0, sort_field="created_at", sort_order=-1):
    cursor = claims_collection.find().sort(sort_field, sort_order)
    if skip:
        cursor = cursor.skip(int(skip))
    if limit:
        cursor = cursor.limit(int(limit))
    claims = list(cursor)
    for claim in claims:
        claim["status"] = normalize_claim_status(claim.get("status"))
    return claims
