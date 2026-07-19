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

        if clauses:
            top_match = clauses[0]
            similarity = round(float(top_match.get("confidence", 0) or 0) * 100, 1)
            
            # Specialty boost logic
            match_text = " ".join(
                str(value).lower()
                for value in [
                    memory.entities.get("surgery"),
                    memory.entities.get("procedure"),
                    memory.entities.get("diagnosis"),
                    memory.entities.get("treatment"),
                    memory.entities.get("speciality"),
                    memory.entities.get("specialty"),
                ]
                if value
            )
            rule_text = str(top_match.get("matched_rule", "")).lower()
            if match_text and any(token in rule_text for token in match_text.split()):
                specialty_boost = 12.5
            elif any(k in rule_text for k in ["specialty", "speciality", "procedure", "surgery", "treatment"]):
                specialty_boost = 8.0
            else:
                specialty_boost = 0.0
                
            score = min(100.0, similarity + specialty_boost)
            
            policy_result["source_document"] = top_match.get("source_document", "")
            policy_result["matched_rule"] = top_match.get("matched_rule", "")
            policy_result["similarity_score"] = score
            
            recommended = score >= 62
            conditional = 48 <= score < 62
            policy_result["status"] = "Recommended" if recommended else "Conditional" if conditional else "Not Recommended"
            policy_result["eligible"] = recommended
            
            explanation = policy_result.get("reasoning", "")
            if not explanation or "pending" in explanation.lower() or "could not be completed" in explanation.lower():
                explanation = f"The submitted claim details were compared with Annexure I and Annexure IA. The closest government rule match is from {top_match.get('source_document', 'the annexure rules')} with {similarity}% similarity. Specialty alignment contributed a {specialty_boost}% boost, bringing the score to {score}%. The claim is marked {policy_result['status'].lower()} for officer verification."
            policy_result["llm_explanation"] = explanation

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

