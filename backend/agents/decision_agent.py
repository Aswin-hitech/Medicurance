from __future__ import annotations

from memory.claim_memory import ClaimMemory

from .base_agent import BaseClaimAgent


class DecisionAgent(BaseClaimAgent):
    name = "decision"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        trust_score = float(memory.trust_result.get("score", memory.confidence_score) or 0.0)
        fraud_score = float(memory.fraud_result.get("fraud_score", 0.0) or 0.0)
        duplicate_score = float(memory.duplicate_result.get("duplicate_probability", 0.0) or 0.0)
        policy_confidence = float(memory.policy_result.get("confidence", 0.0) or 0.0)
        hospital_verified = bool(memory.hospital_verification.get("verified"))
        missing_entities = memory.missing_entity_fields()

        if memory.routing_hint == "request_documents":
            decision = "Request Additional Documents"
            status = "Pending"
            reasoning = "The workflow found missing verification data and needs more support material."
        elif not hospital_verified:
            decision = "Escalate"
            status = "Escalated"
            reasoning = "Hospital verification could not be completed."
        elif fraud_score >= 0.7 or duplicate_score >= 70.0:
            decision = "Reject"
            status = "Rejected"
            reasoning = "Fraud or duplicate risk is too high for automated approval."
        elif trust_score >= 85.0 and policy_confidence >= 0.65 and not missing_entities:
            decision = "Approve"
            status = "Approved"
            reasoning = "The claim has high trust, adequate policy support, and no major contradictions."
        else:
            decision = "Escalate"
            status = "Escalated"
            reasoning = "The claim is valid enough for human review but not strong enough for straight-through approval."

        memory.add_decision(decision, reasoning, status=status)
        memory.metadata[f"{self.name}_confidence"] = trust_score / 100.0
        memory.reasoning_summaries[self.name] = reasoning

        return memory
