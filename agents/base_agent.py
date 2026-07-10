from __future__ import annotations

from abc import ABC, abstractmethod
from time import perf_counter
from uuid import uuid4
from typing import Any

from memory.claim_memory import ClaimMemory
from utils.logger import logger


class BaseClaimAgent(ABC):
    name = "agent"

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        memory = ClaimMemory.from_state(state)
        execution_id = str(uuid4())
        started = perf_counter()
        logger.info("[Agent Started] %s | claim_id=%s", self.name, memory.claim_id)

        try:
            updated_memory = self.run(memory)
            if isinstance(updated_memory, ClaimMemory):
                memory = updated_memory
            elif isinstance(updated_memory, dict):
                memory = ClaimMemory.from_state(updated_memory)
            else:
                memory.add_error(self.name, "Agent returned an unsupported result payload.")
                memory.add_trace(
                    agent=self.name,
                    status="failed",
                    duration_ms=(perf_counter() - started) * 1000.0,
                    error="unsupported result payload",
                    execution_id=execution_id,
                )
                logger.warning("[Agent Finished] %s returned unsupported payload", self.name)
                return memory.to_state()

            duration_ms = (perf_counter() - started) * 1000.0
            confidence = memory.metadata.get(f"{self.name}_confidence", memory.confidence)
            summary = memory.reasoning_summaries.get(self.name, "")
            memory.metadata[f"{self.name}_execution_id"] = execution_id
            memory.metadata[f"{self.name}_execution_time_ms"] = round(duration_ms, 2)
            memory.add_trace(
                agent=self.name,
                status="finished",
                duration_ms=duration_ms,
                confidence=float(confidence or 0.0),
                retries=int(memory.retries.get(self.name, 0) or 0),
                summary=summary,
                execution_id=execution_id,
                metadata={
                    "sources": list(memory.source_references),
                    "warnings": list(memory.warnings),
                    "metadata": dict(memory.metadata),
                },
            )
            logger.info(
                "[Agent Finished] %s | claim_id=%s | duration_ms=%.2f | confidence=%.2f",
                self.name,
                memory.claim_id,
                duration_ms,
                float(confidence or 0.0),
            )
            return memory.to_state()
        except Exception as exc:  # pragma: no cover - defensive guard
            duration_ms = (perf_counter() - started) * 1000.0
            memory.add_error(self.name, str(exc), retryable=True)
            memory.add_trace(
                agent=self.name,
                status="failed",
                duration_ms=duration_ms,
                confidence=float(memory.confidence or 0.0),
                retries=int(memory.retries.get(self.name, 0) or 0),
                error=str(exc),
                execution_id=execution_id,
            )
            logger.exception("[Agent Failed] %s | claim_id=%s", self.name, memory.claim_id)
            return memory.to_state()

    @abstractmethod
    def run(self, memory: ClaimMemory) -> ClaimMemory | dict[str, Any]:
        raise NotImplementedError
