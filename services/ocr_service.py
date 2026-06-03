"""
services/ocr_service.py
Phase 3 — Advanced OCR Pipeline
Uses OCR.Space API with OpenCV preprocessing.
Fallback: pdfplumber → pdf2image → OCR.Space
"""
import os
import io
import base64
import logging
import tempfile
import time
import cv2
import numpy as np
import pdfplumber
from PIL import Image, ImageEnhance, ImageFilter
from pdf2image import convert_from_path

from config.settings import Config

logger = logging.getLogger(__name__)

OCR_SPACE_URL = "https://api.ocr.space/parse/image"


# ─────────────────────────────────────────────
# Image Preprocessing (OpenCV)
# ─────────────────────────────────────────────

def preprocess_image_cv(pil_image: Image.Image) -> Image.Image:
    """
    Apply OpenCV-based preprocessing pipeline to improve OCR accuracy:
    grayscale → denoise → contrast enhance → adaptive threshold → sharpen → resize
    """
    # Convert PIL → OpenCV (BGR)
    img_array = np.array(pil_image.convert("RGB"))
    img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

    # 1. Grayscale
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    # 2. Denoise
    denoised = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

    # 3. Contrast Enhancement (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)

    # 4. Adaptive Threshold (handles variable illumination)
    thresh = cv2.adaptiveThreshold(
        enhanced, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 11, 2
    )

    # 5. Sharpening kernel
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(thresh, -1, kernel)

    # 6. Resize to standard DPI width (2480px = A4 at 300 DPI) if small
    h, w = sharpened.shape
    if w < 1000:
        scale = 1800 / w
        sharpened = cv2.resize(sharpened, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # Convert back to PIL
    return Image.fromarray(sharpened)


# ─────────────────────────────────────────────
# OCR.Space API Call
# ─────────────────────────────────────────────

def _call_ocr_space(image: Image.Image, language: str = "eng") -> dict:
    """
    Call the OCR.Space REST API with a preprocessed PIL image.
    Returns parsed JSON response dict.
    """
    try:
        import requests
    except Exception as exc:
        logger.warning(f"[OCR] Requests library unavailable: {exc}")
        return {}

    # Convert PIL image to JPEG bytes in memory
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=95)
    buffer.seek(0)
    img_bytes = buffer.read()

    # Encode as base64 for the API
    b64_image = base64.b64encode(img_bytes).decode("utf-8")
    b64_payload = f"data:image/jpeg;base64,{b64_image}"

    payload = {
        "base64Image": b64_payload,
        "language": language,
        "isOverlayRequired": False,
        "detectOrientation": True,
        "scale": True,
        "isTable": False,
        "OCREngine": 2,       # Engine 2 — higher accuracy
        "filetype": "JPG",
    }

    headers = {"apikey": Config.OCR_SPACE_API_KEY}

    for attempt in range(3):
        try:
            response = requests.post(OCR_SPACE_URL, data=payload, headers=headers, timeout=20)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.warning("[OCR] OCR.Space attempt %s failed: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    logger.error("[OCR] OCR.Space API call failed after retries.")
    return {}


def _parse_ocr_response(ocr_json: dict) -> tuple[str, float]:
    """
    Extract text and confidence score from OCR.Space JSON response.
    Returns (text, confidence_score_0_to_100)
    """
    if not ocr_json or ocr_json.get("IsErroredOnProcessing"):
        err = ocr_json.get("ErrorMessage", ["Unknown error"]) if ocr_json else ["No response"]
        logger.warning(f"[OCR] OCR.Space returned an error: {err}")
        return "", 0.0

    parsed_results = ocr_json.get("ParsedResults", [])
    if not parsed_results:
        return "", 0.0

    all_text_parts = []
    confidence_sum = 0.0
    count = 0

    for result in parsed_results:
        text = result.get("ParsedText", "").strip()
        if text:
            all_text_parts.append(text)
        # OCR.Space returns MeanConfidence as a percentage string or float
        mean_conf = result.get("TextOverlay", {})
        # Engine 2 does not always return per-word confidence; use exit code as proxy
        exit_code = result.get("FileParseExitCode", 0)
        if exit_code == 1:
            confidence_sum += 85.0   # Successful parse
        elif exit_code == 2:
            confidence_sum += 60.0   # Partial
        else:
            confidence_sum += 30.0   # Failed/timeout
        count += 1

    full_text = "\n".join(all_text_parts)
    avg_confidence = confidence_sum / count if count > 0 else 0.0

    return full_text, avg_confidence


# ─────────────────────────────────────────────
# Text Quality Score
# ─────────────────────────────────────────────

def _compute_text_quality(text: str) -> float:
    """
    Measure OCR text quality as ratio of alphanumeric chars to total chars.
    Returns score 0.0–100.0
    """
    if not text:
        return 0.0
    total = len(text)
    alphanum = sum(1 for c in text if c.isalnum() or c.isspace())
    return round((alphanum / total) * 100, 1)


# ─────────────────────────────────────────────
# Clean OCR Output
# ─────────────────────────────────────────────

def _clean_text(raw: str) -> str:
    """Collapse whitespace, remove null bytes, strip leading/trailing space."""
    text = raw.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    # Collapse runs of blank lines to a single newline
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


# ─────────────────────────────────────────────
# PDF Extraction
# ─────────────────────────────────────────────

def _extract_pdf_text(file_path: str) -> tuple[str, float, str, int]:
    """
    Attempt pdfplumber first; fall back to pdf2image + OCR.Space.
    Returns (text, ocr_confidence, extraction_method)
    """
    # ── 1. pdfplumber (digital/searchable PDFs) ──
    raw_text = ""
    pdf_page_count = 0
    try:
        with pdfplumber.open(file_path) as pdf:
            pdf_page_count = len(pdf.pages)
            for page in pdf.pages:
                content = page.extract_text() or ""
                raw_text += content + "\n"
    except Exception as exc:
        logger.warning(f"[OCR] pdfplumber failed: {exc}")

    raw_text = raw_text.strip()
    if len(raw_text) >= 80:
        logger.info("[OCR] pdfplumber extracted sufficient text.")
        cleaned = _clean_text(raw_text)
        quality = _compute_text_quality(cleaned)
        return cleaned, quality, "pdfplumber", max(pdf_page_count, 1)

    # ── 2. Fallback: pdf2image → OpenCV → OCR.Space ──
    logger.info("[OCR] pdfplumber text sparse — switching to image OCR pipeline.")
    all_text = []
    total_conf = 0.0
    page_count = 0

    try:
        images = convert_from_path(
            file_path,
            dpi=300,
            fmt="jpeg",
            poppler_path=Config.POPPLER_PATH,
        )
    except Exception as exc:
        logger.error(f"[OCR] pdf2image conversion failed: {exc}")
        return _clean_text(raw_text), 0.0, "pdfplumber_partial", max(pdf_page_count, 1)

    for idx, pil_img in enumerate(images):
        page_count += 1
        processed = preprocess_image_cv(pil_img)
        ocr_json = _call_ocr_space(processed)
        page_text, confidence = _parse_ocr_response(ocr_json)
        if page_text:
            all_text.append(f"[Page {idx + 1}]\n{page_text}")
        total_conf += confidence

    merged = _clean_text("\n\n".join(all_text))
    avg_conf = total_conf / page_count if page_count > 0 else 0.0
    return merged, avg_conf, f"ocr_space_{page_count}pages", page_count


# ─────────────────────────────────────────────
# Image Extraction
# ─────────────────────────────────────────────

def _extract_image_text(file_path: str) -> tuple[str, float, str, int]:
    """
    Preprocess a JPG/PNG file and run OCR.Space.
    Returns (text, ocr_confidence, extraction_method)
    """
    try:
        pil_img = Image.open(file_path)
    except Exception as exc:
        logger.error(f"[OCR] Cannot open image {file_path}: {exc}")
        return "", 0.0, "error", 0

    processed = preprocess_image_cv(pil_img)
    ocr_json = _call_ocr_space(processed)
    text, confidence = _parse_ocr_response(ocr_json)
    cleaned = _clean_text(text)
    return cleaned, confidence, "ocr_space_image", 1


# ─────────────────────────────────────────────
# Public Entry Point
# ─────────────────────────────────────────────

def extract_text_advanced(file_path: str) -> dict:
    """
    Main OCR entry point.

    Returns a dict:
    {
        "text":                 str,
        "ocr_confidence":       float (0–100),
        "text_quality_score":   float (0–100),
        "extraction_method":    str,
        "page_count":           int
    }
    """
    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            text, confidence, method, page_count = _extract_pdf_text(file_path)
        elif ext in {".jpg", ".jpeg", ".png"}:
            text, confidence, method, page_count = _extract_image_text(file_path)
        else:
            logger.warning(f"[OCR] Unsupported file type: {ext}")
            text, confidence, method, page_count = "", 0.0, "unsupported", 0
    except Exception as exc:
        logger.error("[OCR] Extraction failed gracefully: %s", exc)
        text, confidence, method, page_count = "", 0.0, "failed", 0

    quality = _compute_text_quality(text)
    if page_count <= 0 and text:
        page_count = 1

    logger.info(
        f"[OCR] Extraction complete | method={method} | "
        f"confidence={confidence:.1f}% | quality={quality:.1f}% | chars={len(text)}"
    )

    return {
        "text": text,
        "ocr_confidence": round(confidence, 1),
        "text_quality_score": round(quality, 1),
        "extraction_method": method,
        "available": bool(text),
        "page_count": page_count,
    }
