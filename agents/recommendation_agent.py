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

        # Calculate completeness score based on attachments
        uploaded_attachments = memory.form_data.get("attachments") or {}
        required_attachments = ["prescriptions", "discharge_summary", "investigation_reports", "id_proof", "ppo_proof"]
        present_count = 1 # medical bills is always present
        
        # Check passport photo
        if memory.form_data.get("existing_passport_photo") or uploaded_attachments.get("passport_photo") or memory.form_data.get("passport_photo"):
            present_count += 1
            
        for att in required_attachments:
            if uploaded_attachments.get(att) or memory.form_data.get(att):
                present_count += 1
                
        if memory.form_data.get("emergency_case") == "Yes" and (uploaded_attachments.get("certificates") or memory.form_data.get("certificates")):
            present_count += 1
            max_possible = 8
        else:
            max_possible = 7
            
        completeness_score = round((present_count / max_possible) * 100.0, 1)

        # Retrieve intermediate agent results
        trust_score = float(memory.trust_result.get("score", memory.confidence_score) or 0.0)
        fraud_score = float(memory.fraud_result.get("fraud_score", 0.0) or 0.0)
        rule_matching_score = float(memory.policy_result.get("similarity_score", 0.0) or 0.0)

        # Combined AI recommendation score (Eligibility Score)
        # Combination of rule match score (40%), trust score (40%), and low fraud risk (20%)
        eligibility_score = round(
            (rule_matching_score * 0.4) + 
            (trust_score * 0.4) + 
            ((100.0 - fraud_score * 100.0) * 0.2), 
            1
        )

        # Explainable AI Summary
        doc_msg = f"contains {present_count} out of {max_possible} required documents"
        ocr_msg = f"The uploaded documents are scanned clearly with {memory.ocr_confidence}% readability"
        policy_msg = f"The treatment matches government reimbursement guidelines with a similarity of {rule_matching_score}%"
        
        dup_prob = memory.duplicate_result.get('duplicate_probability', 0.0)
        if dup_prob > 30:
            dup_msg = f"A possible duplicate submission warning ({dup_prob}% probability) was flagged for manual check"
        else:
            dup_msg = "No duplicate submissions were detected"
            
        fraud_val = round(fraud_score * 100.0, 1)
        if fraud_val > 50:
            fraud_msg = f"a fraud warning score of {fraud_val}% was flagged, requiring review"
        else:
            fraud_msg = "fraud risk checks are clean"

        explainable_ai_summary = (
            f"This claim ({memory.claim_id[-8:]}) has been checked: it {doc_msg}. "
            f"{ocr_msg}. {policy_msg}. {dup_msg}, and {fraud_msg}. "
            f"Based on these checks, the claim shows a trust rating of {trust_score}% and an overall eligibility score of {eligibility_score}%."
        )

        # Officer Recommendation
        if memory.decision == "Approve":
            officer_recommendation = "Approved. Proceed with reimbursement check."
        elif memory.decision == "Reject":
            officer_recommendation = f"Reject claim. Primary reasons: {memory.metadata.get('decision_reasoning', 'High fraud risk or duplicate detected.')}"
        elif memory.decision == "Request Additional Documents":
            officer_recommendation = "Hold claim. Request beneficiary to upload missing documents."
        else:
            officer_recommendation = "Manual Review. Escalate to senior officer due to unresolved contradictions."

        recommendation = {
            "decision": memory.decision,
            "status": memory.status,
            "summary": memory.summarize(),
            "missing_documents": sorted({item for item in missing_documents if item}),
            "next_steps": next_steps,
            "reasoning": memory.metadata.get("decision_reasoning", ""),
            "source_references": list(memory.source_references),
            "ocr_confidence": memory.ocr_confidence,
            "trust_score": trust_score,
            "eligibility_score": eligibility_score,
            "fraud_score": round(fraud_score * 100.0, 1),
            "document_completeness_score": completeness_score,
            "rule_matching_score": rule_matching_score,
            "explainable_ai_summary": explainable_ai_summary,
            "officer_recommendation": officer_recommendation,
        }

        memory.recommendation = recommendation
        memory.reasoning_summaries[self.name] = recommendation["summary"]
        memory.add_source_reference("decision_agent")
        memory.add_source_reference("recommendation_agent")
        return memory

