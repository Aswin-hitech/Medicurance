from __future__ import annotations

from memory.claim_memory import ClaimMemory
from services.ocr_service import extract_text_advanced
from utils.logger import logger

from .base_agent import BaseClaimAgent


class OCRAgent(BaseClaimAgent):
    name = "ocr"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        if not memory.temp_path:
            memory.ocr_output = {
                "text": "",
                "ocr_confidence": 0.0,
                "text_quality_score": 0.0,
                "extraction_method": "missing_input",
                "available": False,
                "page_count": 0,
            }
            memory.extracted_text = ""
            memory.ocr_confidence = 0.0
            memory.ocr_page_count = 0
            memory.routing_hint = "request_documents"
            memory.reasoning_summaries[self.name] = "No claim document was available for OCR."
            memory.metadata[f"{self.name}_confidence"] = 0.0
            return memory

        ocr_output = extract_text_advanced(memory.temp_path)
        text = str(ocr_output.get("text", "") or "")
        confidence = float(ocr_output.get("ocr_confidence", 0.0) or 0.0)
        page_count = int(ocr_output.get("page_count", 0) or 0)
        retry_count = int(memory.retries.get(self.name, 0) or 0)
        should_retry = bool(text) and confidence < 45.0 and retry_count < 1

        memory.ocr_output = ocr_output
        memory.extracted_text = text
        memory.ocr_confidence = confidence
        memory.ocr_page_count = page_count
        memory.metadata[f"{self.name}_confidence"] = confidence / 100.0
        memory.routing_hint = "retry_ocr" if should_retry else "continue"
        if should_retry:
            memory.retries[self.name] = retry_count + 1
        memory.reasoning_summaries[self.name] = (
            f"OCR completed with {confidence:.1f}% confidence using {ocr_output.get('extraction_method', 'unknown')}."
        )
        memory.add_source_reference(f"ocr:{ocr_output.get('extraction_method', 'unknown')}")

        if confidence < 45.0:
            memory.warnings.append("Low OCR confidence.")
            logger.info("[OCR] Low confidence detected for claim_id=%s", memory.claim_id)

        return memory
