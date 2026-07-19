from __future__ import annotations

from memory.claim_memory import ClaimMemory
from services.duplicate_detection_service import analyze_duplicate_claim

from .base_agent import BaseClaimAgent


class DuplicateAgent(BaseClaimAgent):
    name = "duplicate"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        duplicate_result = memory.duplicate_result or memory.fraud_result.get("duplicate_result", {})
        if not duplicate_result and memory.temp_path:
            duplicate_result = analyze_duplicate_claim(
                mobile=memory.mobile,
                amount=memory.amount,
                hospital=memory.hospital,
                extracted_text=memory.extracted_text,
                file_hash=memory.fraud_result.get("duplicate_hash"),
                image_hash=memory.fraud_result.get("image_hash"),
                entities=memory.entities,
            )

        memory.duplicate_result = duplicate_result or {}
        memory.metadata[f"{self.name}_confidence"] = max(0.0, 1.0 - float(memory.duplicate_result.get("duplicate_probability", 0.0) or 0.0) / 100.0)
        memory.reasoning_summaries[self.name] = (
            f"Duplicate probability is {memory.duplicate_result.get('duplicate_probability', 0.0):.1f}%."
        )
        if float(memory.duplicate_result.get("duplicate_probability", 0.0) or 0.0) >= 70.0:
            memory.routing_hint = "manual_review"

        return memory

