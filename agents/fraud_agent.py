from __future__ import annotations

from memory.claim_memory import ClaimMemory
from services.fraud_service import detect_fraud

from .base_agent import BaseClaimAgent


class FraudAgent(BaseClaimAgent):
    name = "fraud"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        text = memory.extracted_text or str(memory.ocr_output.get("text", "") or "")
        fraud_result = detect_fraud(
            memory.mobile,
            memory.amount,
            memory.hospital,
            memory.temp_path,
            text,
        )
        memory.fraud_result = fraud_result
        if fraud_result.get("duplicate_result"):
            memory.duplicate_result = fraud_result.get("duplicate_result", {})
        memory.metadata[f"{self.name}_confidence"] = max(0.0, 1.0 - float(fraud_result.get("fraud_score", 0.0) or 0.0))
        memory.reasoning_summaries[self.name] = (
            f"Fraud analysis flagged {len(fraud_result.get('fraud_flags', []))} issue(s) with risk level {fraud_result.get('fraud_level', 'LOW')}."
        )
        memory.add_source_reference("fraud_service")

        if float(fraud_result.get("fraud_score", 0.0) or 0.0) >= 0.7:
            memory.routing_hint = "manual_review"

        return memory

