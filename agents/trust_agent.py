from __future__ import annotations

from memory.claim_memory import ClaimMemory
from services.trust_service import calculate_trust_score

from .base_agent import BaseClaimAgent


class TrustAgent(BaseClaimAgent):
    name = "trust"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        trust_result = calculate_trust_score(
            mobile=memory.mobile,
            hospital_name=memory.hospital,
            ai_confidence=float(memory.policy_result.get("confidence", 0.0) or 0.0),
            ocr_confidence=float(memory.ocr_confidence or 0.0) / 100.0 if memory.ocr_confidence > 1 else float(memory.ocr_confidence or 0.0),
            image_hash=memory.fraud_result.get("image_hash", ""),
            duplicate_result=memory.duplicate_result or memory.fraud_result.get("duplicate_result", {}),
        )
        memory.trust_result = trust_result
        memory.confidence_score = float(trust_result.get("score", 0.0) or 0.0)
        memory.confidence = memory.confidence_score
        memory.metadata[f"{self.name}_confidence"] = memory.confidence_score / 100.0
        memory.reasoning_summaries[self.name] = (
            f"Trust score computed at {memory.confidence_score:.1f} ({trust_result.get('level', 'LOW')})."
        )
        memory.add_source_reference("trust_service")

        if memory.confidence_score < 60.0:
            memory.routing_hint = "manual_review"

        return memory

