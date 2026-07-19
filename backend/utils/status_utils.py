def normalize_claim_status(status):
    """
    Normalize legacy claim statuses into the supported workflow values.
    """
    if not status:
        return "Pending"

    normalized = str(status).strip().title()
    if normalized == "Submitted":
        return "Pending"
    if normalized in {"Pending", "Approved", "Rejected", "Escalated"}:
        return normalized
    return "Pending"
