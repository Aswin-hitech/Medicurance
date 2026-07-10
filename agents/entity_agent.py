from __future__ import annotations

from memory.claim_memory import ClaimMemory
from services.entity_extractor import extract_entities
from services.duplicate_detection_service import extract_duplicate_entities

from .base_agent import BaseClaimAgent


class EntityAgent(BaseClaimAgent):
    name = "entity"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        text = memory.extracted_text or str(memory.ocr_output.get("text", "") or "")
        if not text.strip():
            memory.entities = {field: None for field in [
                "patient_name",
                "hospital_name",
                "claim_amount",
                "invoice_number",
                "admission_date",
                "discharge_date",
                "doctor_name",
            ]}
            memory.entities.update({"extraction_source": "empty_text", "fields_extracted": 0})
            memory.reasoning_summaries[self.name] = "Entity extraction skipped because OCR returned no text."
            memory.routing_hint = "request_documents"
            memory.metadata[f"{self.name}_confidence"] = 0.0
            return memory

        entities = extract_entities(text)
        duplicate_entities = extract_duplicate_entities(text)
        for key, value in duplicate_entities.items():
            if value and not entities.get(key):
                entities[key] = value

        missing_fields = [field for field in [
            "patient_name",
            "hospital_name",
            "claim_amount",
            "invoice_number",
            "admission_date",
            "discharge_date",
            "doctor_name",
        ] if not entities.get(field)]
        filled = int(entities.get("fields_extracted", 0) or 0)
        total = 7
        confidence = round((filled / total) * 100.0, 1)
        retry_count = int(memory.retries.get(self.name, 0) or 0)

        memory.entities = entities
        memory.metadata["missing_entities"] = missing_fields
        memory.metadata[f"{self.name}_confidence"] = confidence / 100.0
        memory.routing_hint = "retry_entities" if missing_fields and retry_count < 1 else "continue"
        if missing_fields and retry_count < 1:
            memory.retries[self.name] = retry_count + 1
            memory.warnings.append(f"Missing entities: {', '.join(missing_fields)}")
        memory.reasoning_summaries[self.name] = (
            f"Entity extraction found {filled}/{total} key fields; missing: {', '.join(missing_fields) or 'none'}."
        )
        if duplicate_entities.get("invoice_number"):
            memory.add_source_reference("duplicate_entities:invoice_number")

        return memory
