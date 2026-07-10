from __future__ import annotations

from memory.claim_memory import ClaimMemory

from .base_agent import BaseClaimAgent


class RecommendationAgent(BaseClaimAgent):
    name = "recommendation"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        missing_documents = []
        if memory.routing_hint == "request_documents":
            missing_documents.extend(["Hospital verification", "Additional claim evidence"])
        if memory.missing_entity_fields():
            missing_documents.extend(memory.missing_entity_fields())

        next_steps: list[str] = []
        if memory.decision == "Approve":
            next_steps.append("Move the claim to officer review for final issuance of approval letter.")
        elif memory.decision == "Reject":
            next_steps.append("Prepare a rejection note with the fraud and duplicate findings.")
        elif memory.decision == "Request Additional Documents":
            next_steps.append("Ask the claimant to submit the missing verification documents.")
        else:
            next_steps.append("Escalate the claim to an officer for manual review.")

        recommendation = {
            "decision": memory.decision,
            "status": memory.status,
            "summary": memory.summarize(),
            "missing_documents": sorted({item for item in missing_documents if item}),
            "next_steps": next_steps,
            "reasoning": memory.metadata.get("decision_reasoning", ""),
            "source_references": list(memory.source_references),
        }

        memory.recommendation = recommendation
        memory.reasoning_summaries[self.name] = recommendation["summary"]
        memory.add_source_reference("decision_agent")
        memory.add_source_reference("recommendation_agent")
        return memory

