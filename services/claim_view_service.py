from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from utils.status_utils import normalize_claim_status


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def claim_metrics(claim: Dict[str, Any]) -> Dict[str, Any]:
    trust_result = claim.get("trust_result") or {}
    ai_result = claim.get("ai_result") or {}
    fraud_result = claim.get("fraud_result") or {}
    duplicate_result = claim.get("duplicate_result") or {}
    status = normalize_claim_status(claim.get("status"))

    risk_level = (
        fraud_result.get("fraud_level")
        or ai_result.get("system_risk_level")
        or ai_result.get("risk_level")
        or "LOW"
    )
    hospital_verified = (
        claim.get("hospital_verified")
        or fraud_result.get("hospital_verified")
        or ai_result.get("hospital_verified")
    )

    return {
        "status": status,
        "trust_score": round(_as_float(trust_result.get("score", claim.get("trust_score"))), 1),
        "trust_level": str(trust_result.get("level", claim.get("trust_level", "LOW"))).upper(),
        "fraud_level": str(risk_level).upper(),
        "fraud_score": round(_as_float(fraud_result.get("fraud_score", ai_result.get("fraud_score"))), 2),
        "ocr_confidence": round(_as_float(claim.get("ocr_confidence", claim.get("ocr_confidence_score"))), 1),
        "duplicate_probability": round(_as_float(duplicate_result.get("duplicate_probability", claim.get("duplicate_probability"))), 1),
        "hospital_verification": "Verified" if hospital_verified else "Manual Review",
        "government_verification": "Verified" if claim.get("is_government_employee", True) else "Pending",
        "officer_decision": status if status in {"Approved", "Rejected", "Escalated"} else "Pending Review",
        "reference_number": claim.get("letter_reference") or claim.get("claim_reference") or "Pending",
    }


def claim_explanation(claim: Dict[str, Any]) -> str:
    ai_result = claim.get("ai_result") or {}
    trust_result = claim.get("trust_result") or {}
    fraud_result = claim.get("fraud_result") or {}
    metrics = claim_metrics(claim)

    reasoning = ai_result.get("reasoning") or trust_result.get("reasoning")
    if isinstance(reasoning, list):
        reasoning = " ".join(str(item) for item in reasoning if item)
    if reasoning:
        return str(reasoning)

    status = metrics["status"]
    decision = "is awaiting officer review"
    if status == "Approved":
        decision = "was approved"
    elif status == "Rejected":
        decision = "was rejected"
    elif status == "Escalated":
        decision = "was escalated for further verification"

    return (
        f"Based on OCR verification, hospital validation, duplicate analysis, and government "
        f"employee eligibility, the claim achieved a trust score of {metrics['trust_score']}% "
        f"with {metrics['fraud_level']} fraud risk and {decision}."
    )


def claim_timeline(claim: Dict[str, Any]) -> List[Dict[str, str]]:
    status = normalize_claim_status(claim.get("status"))
    created_at = claim.get("created_at") or claim.get("date") or ""
    updated_at = claim.get("updated_at") or created_at
    processing_status = str(claim.get("processing_status") or "").lower()

    items = [
        {
            "label": "Submitted",
            "detail": "Claim submitted by beneficiary.",
            "state": "done",
            "timestamp": created_at,
            "icon": "fa-file-circle-plus",
        },
        {
            "label": "OCR Completed",
            "detail": "Uploaded bill scanned and extracted.",
            "state": "done" if claim.get("extracted_text") or claim.get("ocr_confidence") else "pending",
            "timestamp": created_at,
            "icon": "fa-file-lines",
        },
        {
            "label": "AI Analysis",
            "detail": "Eligibility, fraud, duplicate, and trust checks generated.",
            "state": "done" if claim.get("ai_result") or processing_status == "completed" else "pending",
            "timestamp": created_at,
            "icon": "fa-brain",
        },
        {
            "label": "Officer Review",
            "detail": "Government officer review and remarks.",
            "state": "done" if status in {"Approved", "Rejected", "Escalated"} else "active",
            "timestamp": updated_at if status in {"Approved", "Rejected", "Escalated"} else "",
            "icon": "fa-user-shield",
        },
        {
            "label": status if status in {"Approved", "Rejected"} else "Decision Pending",
            "detail": "Official communication issued." if status in {"Approved", "Rejected"} else "Final decision not yet issued.",
            "state": "done" if status in {"Approved", "Rejected"} else "pending",
            "timestamp": updated_at if status in {"Approved", "Rejected"} else "",
            "icon": "fa-circle-check" if status == "Approved" else "fa-circle-xmark" if status == "Rejected" else "fa-hourglass-half",
        },
    ]
    return items


def enrich_claim_for_view(claim: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(claim or {})
    enriched["status"] = normalize_claim_status(enriched.get("status"))
    enriched["metrics"] = claim_metrics(enriched)
    enriched["timeline"] = claim_timeline(enriched)
    enriched["ai_reasoning_summary"] = claim_explanation(enriched)
    enriched["letter_reference"] = enriched.get("letter_reference") or enriched["metrics"]["reference_number"]
    return enriched


def display_date(value: Any) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return str(value)
