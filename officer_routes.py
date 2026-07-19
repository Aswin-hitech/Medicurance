from flask import render_template
from database.mongo_client import claims_collection


def officer_dashboard():

    claims = list(claims_collection.find().sort("created_at", -1))
    
    pending_count = claims_collection.count_documents({"status": {"$in": ["Pending", "Submitted"]}})
    approved_count = claims_collection.count_documents({"status": "Approved"})
    rejected_count = claims_collection.count_documents({"status": "Rejected"})
    escalated_count = claims_collection.count_documents({"status": "Escalated"})

    stats = {
        "approved_claims": approved_count,
        "rejected_claims": rejected_count,
        "escalated_claims": escalated_count
    }

    return render_template(
        "officer_dashboard.html",
        claims=claims,
        pending_count=pending_count,
        stats=stats
    )


def approve_claim(claim_id):

    claims_collection.update_one(
        {"claim_id": claim_id},
        {"$set": {"status": "Approved"}}
    )


def reject_claim(claim_id):

    claims_collection.update_one(
        {"claim_id": claim_id},
        {"$set": {"status": "Rejected"}}
    )

def claim_review_page(claim_id):

    claim = claims_collection.find_one(
        {"claim_id": claim_id}
    )

    return render_template(
        "officer_claim_review.html",
        claim=claim
    )
