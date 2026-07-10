from __future__ import annotations

from memory.claim_memory import ClaimMemory
from services.rag_service import rag_validate, retrieve_rules

from .base_agent import BaseClaimAgent


class PolicyAgent(BaseClaimAgent):
    name = "policy"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        text = memory.extracted_text or str(memory.ocr_output.get("text", "") or "")
        query_parts = [
            memory.entities.get("surgery"),
            memory.entities.get("procedure"),
            memory.entities.get("diagnosis"),
            memory.entities.get("treatment"),
            memory.entities.get("summary"),
            text[:800],
        ]
        query = " ".join(str(part) for part in query_parts if part).strip()
        clauses = retrieve_rules(query or text[:800], k=5) if (query or text) else []
        policy_result = rag_validate(text, entities=memory.entities)

        policy_confidence = float(policy_result.get("confidence", 0.0) or 0.0)
        memory.policy_clauses = clauses
        memory.policy_result = policy_result
        memory.metadata[f"{self.name}_confidence"] = policy_confidence
        memory.reasoning_summaries[self.name] = (
            f"Policy retrieval returned {len(clauses)} clause(s) and the RAG validator scored confidence at {policy_confidence:.2f}."
        )
        for clause in clauses:
            memory.add_source_reference(str(clause.get("source_document", "policy_clause")))

        if not clauses:
            memory.routing_hint = "manual_review"

        return memory

