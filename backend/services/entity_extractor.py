"""
services/entity_extractor.py
Phase 4 — Document Entity Extraction
Extracts clinical and financial entities from OCR text using
Regex patterns first, with Groq LLM fallback for missing fields.
"""
import re
import json
import logging
from services.llm_service import ask_llm

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Regex Patterns
# ─────────────────────────────────────────────

# Date patterns: dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy, yyyy-mm-dd, "15 Jan 2024"
_DATE_RE = re.compile(
    r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
    re.IGNORECASE,
)

# Invoice / Bill Number: common labels followed by alphanumeric
_INVOICE_RE = re.compile(
    r"(?:Invoice\s*(?:No\.?|Number|#)|Bill\s*(?:No\.?|Number|#)|Receipt\s*(?:No\.?|#))"
    r"\s*[:\-]?\s*([A-Z0-9\-\/]+)",
    re.IGNORECASE,
)

# Amounts: ₹ or Rs. followed by digits (with optional commas and decimals)
_AMOUNT_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

# Total amount: look for "Total", "Grand Total", "Net Amount", "Amount Due"
_TOTAL_RE = re.compile(
    r"(?:Grand\s+Total|Net\s+Amount|Amount\s+Due|Total\s+Amount|Total)\s*[:\-]?\s*"
    r"(?:₹|Rs\.?)?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

# Doctor name: "Dr." prefix
_DOCTOR_RE = re.compile(
    r"\bDr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})",
    re.IGNORECASE,
)

# Patient name: "Patient Name:", "Patient:", "Name of Patient:"
_PATIENT_RE = re.compile(
    r"(?:Patient\s*Name|Patient|Name\s+of\s+Patient)\s*[:\-]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})",
    re.IGNORECASE,
)

# Hospital name: "Hospital Name:", common suffixes
_HOSPITAL_RE = re.compile(
    r"(?:Hospital\s*Name|Hospital|Clinic|Medical\s+Centre|Health\s+Centre)\s*[:\-]\s*"
    r"([A-Z][A-Za-z\s&\.]+(?:Hospital|Clinic|Centre|Health|Care|Medical)?)",
    re.IGNORECASE,
)

# Admission & discharge dates
_ADMISSION_RE = re.compile(
    r"(?:Admission\s*Date|Date\s+of\s+Admission|Admitted\s+on)\s*[:\-]?\s*"
    r"(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})",
    re.IGNORECASE,
)
_DISCHARGE_RE = re.compile(
    r"(?:Discharge\s*Date|Date\s+of\s+Discharge|Discharged\s+on)\s*[:\-]?\s*"
    r"(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})",
    re.IGNORECASE,
)

# Medicine names: look after "Tablet", "Cap.", "Syrup", "Injection", "Inj."
_MEDICINE_RE = re.compile(
    r"(?:Tablet|Tab\.?|Capsule|Cap\.?|Syrup|Injection|Inj\.?|Cream|Ointment)\s+"
    r"([A-Z][a-zA-Z0-9\-\(\)\s]{2,40})",
    re.IGNORECASE,
)

# Procedure names: look after "Procedure:", "Surgery:", "Operation:", "Treatment:"
_PROCEDURE_RE = re.compile(
    r"(?:Procedure|Surgery|Operation|Treatment|Service)\s*[:\-]\s*"
    r"([A-Z][a-zA-Z0-9\s\-\/]{3,60})",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────
# Regex-based Extraction
# ─────────────────────────────────────────────

def _extract_with_regex(text: str) -> dict:
    entities = {}

    # Patient name
    m = _PATIENT_RE.search(text)
    if m:
        entities["patient_name"] = m.group(1).strip().title()

    # Hospital name
    m = _HOSPITAL_RE.search(text)
    if m:
        entities["hospital_name"] = m.group(1).strip().title()

    # Invoice / Bill number
    m = _INVOICE_RE.search(text)
    if m:
        entities["invoice_number"] = m.group(1).strip().upper()

    # Admission / Discharge dates
    m = _ADMISSION_RE.search(text)
    if m:
        entities["admission_date"] = m.group(1).strip()

    m = _DISCHARGE_RE.search(text)
    if m:
        entities["discharge_date"] = m.group(1).strip()

    # Claim amount — prefer grand total
    total_m = _TOTAL_RE.search(text)
    if total_m:
        raw = total_m.group(1).replace(",", "").strip()
        try:
            entities["claim_amount"] = float(raw)
        except ValueError:
            pass

    if "claim_amount" not in entities:
        amounts = _AMOUNT_RE.findall(text)
        if amounts:
            try:
                parsed = [float(a.replace(",", "")) for a in amounts]
                entities["claim_amount"] = max(parsed)   # Largest amount likely the total
            except ValueError:
                pass

    # Doctor name
    docs = _DOCTOR_RE.findall(text)
    if docs:
        entities["doctor_name"] = docs[0].strip().title()

    # Medicine list
    meds = _MEDICINE_RE.findall(text)
    if meds:
        entities["medicine_names"] = list(dict.fromkeys(
            [m.strip().title() for m in meds if len(m.strip()) > 2]
        ))[:10]

    # Procedure list
    procs = _PROCEDURE_RE.findall(text)
    if procs:
        entities["procedure_names"] = list(dict.fromkeys(
            [p.strip().title() for p in procs if len(p.strip()) > 3]
        ))[:10]

    # All dates found (for reference)
    all_dates = _DATE_RE.findall(text)
    if all_dates:
        entities["all_dates_found"] = list(dict.fromkeys(all_dates))[:10]

    return entities


# ─────────────────────────────────────────────
# LLM Fallback Extraction
# ─────────────────────────────────────────────

def _extract_with_llm(text: str, missing_fields: list[str]) -> dict:
    """
    Ask Groq to extract only the fields that regex couldn't find.
    Returns a dict of extracted values.
    """
    if not missing_fields or not text.strip():
        return {}

    fields_desc = ", ".join(missing_fields)
    prompt = f"""You are a medical document parser. Extract specific information from the medical bill text below.

EXTRACT ONLY these fields (return null if not found): {fields_desc}

Return ONLY valid JSON with these exact keys:
- patient_name (string or null)
- hospital_name (string or null)
- invoice_number (string or null)
- admission_date (string in DD/MM/YYYY format or null)
- discharge_date (string in DD/MM/YYYY format or null)
- claim_amount (number or null)
- doctor_name (string or null)
- medicine_names (array of strings or [])
- procedure_names (array of strings or [])

MEDICAL BILL TEXT:
{text[:3000]}

Return ONLY the JSON object, no markdown, no explanation."""

    try:
        response = ask_llm(prompt, json_mode=True)
        # Clean potential markdown
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r"```(?:json)?", "", response).strip()
        result = json.loads(response)
        return {k: v for k, v in result.items() if v is not None and v != [] and v != ""}
    except Exception as exc:
        logger.warning(f"[Entity] LLM fallback extraction failed: {exc}")
        return {}


# ─────────────────────────────────────────────
# Public Entry Point
# ─────────────────────────────────────────────

EXPECTED_FIELDS = [
    "patient_name", "hospital_name", "invoice_number",
    "admission_date", "discharge_date", "claim_amount",
    "doctor_name", "medicine_names", "procedure_names",
]


def extract_entities(text: str) -> dict:
    """
    Extract clinical and financial entities from OCR text.
    Uses regex first; calls LLM for any missing critical fields.

    Returns:
        dict with all extracted entities plus metadata.
    """
    if not text or not text.strip():
        return {f: None for f in EXPECTED_FIELDS} | {
            "extraction_source": "empty_text",
            "fields_extracted": 0,
        }

    # ── Step 1: Regex ──
    entities = _extract_with_regex(text)

    # ── Step 2: Identify what is missing ──
    primary_fields = ["patient_name", "hospital_name", "claim_amount", "invoice_number",
                      "admission_date", "discharge_date", "doctor_name"]
    missing = [f for f in primary_fields if f not in entities]

    # ── Step 3: LLM fallback for missing fields ──
    if missing:
        logger.info(f"[Entity] Regex missed {len(missing)} fields, using LLM: {missing}")
        llm_results = _extract_with_llm(text, missing)
        for field in missing:
            if field in llm_results:
                entities[field] = llm_results[field]

    # ── Step 4: Fill nulls for any still-missing expected fields ──
    for f in EXPECTED_FIELDS:
        if f not in entities:
            entities[f] = None

    # ── Step 5: Metadata ──
    filled = sum(1 for f in EXPECTED_FIELDS if entities.get(f) is not None)
    entities["extraction_source"] = "regex+llm" if missing else "regex"
    entities["fields_extracted"] = filled

    logger.info(f"[Entity] Extracted {filled}/{len(EXPECTED_FIELDS)} entities via {entities['extraction_source']}")
    return entities
