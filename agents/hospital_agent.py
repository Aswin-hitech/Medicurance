from __future__ import annotations

from memory.claim_memory import ClaimMemory
from database.hospital_repository import verify_hospital

from .base_agent import BaseClaimAgent


class HospitalAgent(BaseClaimAgent):
    name = "hospital"

    def run(self, memory: ClaimMemory) -> ClaimMemory:
        verification = verify_hospital(memory.hospital)
        memory.hospital_verification = verification
        memory.metadata[f"{self.name}_confidence"] = 1.0 if verification.get("verified") else 0.0
        memory.reasoning_summaries[self.name] = (
            "Hospital is verified and within the network."
            if verification.get("verified")
            else "Hospital verification failed and will require officer attention."
        )

        if not verification.get("verified"):
            memory.routing_hint = "request_documents"

        return memory

