from __future__ import annotations

from memory.claim_memory import ClaimMemory

from .base_agent import BaseClaimAgent


class ReflectionAgent(BaseClaimAgent):
    name = "reflection"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        notes: list[str] = []
        if memory.ocr_confidence < 45.0:
            notes.append("OCR confidence is low.")
            memory.warnings.append("OCR confidence below threshold.")
        if memory.missing_entity_fields():
            notes.append("Entity extraction is missing key fields.")
            memory.warnings.append("Entity extraction incomplete.")
        if not memory.hospital_verification.get("verified"):
            notes.append("Hospital verification failed.")
            memory.warnings.append("Hospital verification failed.")
        if float(memory.duplicate_result.get("duplicate_probability", 0.0) or 0.0) >= 70.0:
            notes.append("Duplicate risk is high.")
            memory.warnings.append("Duplicate probability is high.")
        if float(memory.fraud_result.get("fraud_score", 0.0) or 0.0) >= 0.7:
            notes.append("Fraud risk is high.")
            memory.warnings.append("Fraud score exceeds threshold.")
        if float(memory.policy_result.get("confidence", 0.0) or 0.0) < 0.55:
            notes.append("Policy confidence is low.")
            memory.warnings.append("Policy confidence below threshold.")
        if not notes:
            notes.append("No contradictions detected across the current evidence set.")

        memory.reflection_notes = notes
        memory.reasoning_summaries[self.name] = " ".join(notes)
        memory.metadata[f"{self.name}_confidence"] = 1.0 if len(notes) == 1 and "No contradictions" in notes[0] else 0.5

        if memory.ocr_confidence < 45.0 and memory.retries.get("ocr", 0) < 1:
            memory.routing_hint = "retry_ocr"
        elif memory.missing_entity_fields() and memory.retries.get("entity", memory.retries.get("entities", 0)) < 1:
            memory.routing_hint = "retry_entities"
        elif not memory.hospital_verification.get("verified"):
            memory.routing_hint = "request_documents"
        elif float(memory.fraud_result.get("fraud_score", 0.0) or 0.0) >= 0.7:
            memory.routing_hint = "manual_review"
        else:
            memory.routing_hint = "continue"

        return memory
