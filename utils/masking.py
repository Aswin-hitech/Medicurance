import copy
import re


SENSITIVE_FIELD_PATTERNS = (
    "aadhaar",
    "aadhar",
    "account",
    "bank",
    "ifsc",
    "pan",
    "password",
    "otp",
    "secret",
    "token",
)


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def mask_value(field_name, value):
    key = str(field_name or "").lower()
    if value in (None, ""):
        return value
    if isinstance(value, bytes):
        return "<masked binary>"

    text = str(value)
    digits = _digits(text)

    if "aadhaar" in key or "aadhar" in key:
        last4 = digits[-4:] if digits else text[-4:]
        return f"XXXX XXXX {last4}"
    if "account" in key:
        last4 = digits[-4:] if digits else text[-4:]
        return f"******{last4}"
    if "pan" in key:
        return f"****{text[-4:]}" if len(text) >= 4 else "****"
    if "password" in key or "otp" in key or "secret" in key or "token" in key:
        return "<masked>"
    if "ifsc" in key:
        return f"{text[:4]}****" if len(text) > 4 else "****"
    if "bank" in key:
        return text
    return value


def is_sensitive_field(field_name):
    key = str(field_name or "").lower()
    return any(pattern in key for pattern in SENSITIVE_FIELD_PATTERNS)


def mask_document(document):
    masked = copy.deepcopy(document)
    for key, value in list(masked.items()):
        if isinstance(value, dict):
            masked[key] = mask_document(value)
        elif isinstance(value, list):
            masked[key] = [mask_document(item) if isinstance(item, dict) else item for item in value]
        elif is_sensitive_field(key):
            masked[key] = mask_value(key, value)
    return masked
