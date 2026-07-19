from __future__ import annotations

from functools import lru_cache
from typing import Any

from memory.claim_memory import ClaimMemory
from utils.logger import logger

from graphs.claim_graph import run_claim_graph


class OrchestratorAgent:
    name = "orchestrator"

    def execute(self, state: ClaimMemory | dict[str, Any]) -> ClaimMemory:
        memory = state if isinstance(state, ClaimMemory) else ClaimMemory.from_state(state)
        logger.info("[Orchestrator Started] claim_id=%s", memory.claim_id)
        result_state = run_claim_graph(memory.to_state())
        result_memory = ClaimMemory.from_state(result_state)
        logger.info(
            "[Orchestrator Finished] claim_id=%s | decision=%s | status=%s | trust=%.1f",
            result_memory.claim_id,
            result_memory.decision,
            result_memory.status,
            float(result_memory.trust_result.get("score", result_memory.confidence_score) or 0.0),
        )
        logger.info(
            "[Orchestrator RAG] claim_id=%s | similarity=%.1f | eligible=%s",
            result_memory.claim_id,
            float(result_memory.policy_result.get("similarity_score", 0.0) or 0.0),
            bool(result_memory.policy_result.get("eligible", False))
        )
        return result_memory

    def coordinate(self, state: ClaimMemory | dict[str, Any]) -> ClaimMemory:
        return self.execute(state)


@lru_cache(maxsize=1)
def get_orchestrator_agent() -> OrchestratorAgent:
    return OrchestratorAgent()

