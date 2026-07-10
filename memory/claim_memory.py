from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, TypedDict


class ClaimGraphState(TypedDict, total=False):
    claim_id: str
    mobile: str
    name: str
    hospital: str
    amount: float
    bill_url: str
    temp_path: str
    form_data: dict[str, Any]
    ocr_output: dict[str, Any]
    extracted_text: str
    ocr_confidence: float
    ocr_page_count: int
    entities: dict[str, Any]
    policy_clauses: list[dict[str, Any]]
    policy_result: dict[str, Any]
    fraud_result: dict[str, Any]
    hospital_verification: dict[str, Any]
    duplicate_result: dict[str, Any]
    trust_result: dict[str, Any]
    reflection_notes: list[str]
    intermediate_decisions: list[dict[str, Any]]
    recommendation: dict[str, Any]
    decision: str
    routing_hint: str
    confidence: float
    confidence_score: float
    retries: dict[str, int]
    agent_trace: list[dict[str, Any]]
    reasoning_summaries: dict[str, str]
    source_references: list[str]
    errors: list[dict[str, Any]]
    warnings: list[str]
    metadata: dict[str, Any]
    status: str
    created_at: str
    updated_at: str
    final_decision: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ClaimMemory:
    claim_id: str
    mobile: str
    name: str
    hospital: str
    amount: float
    bill_url: str = ""
    temp_path: str = ""
    form_data: dict[str, Any] = field(default_factory=dict)
    ocr_output: dict[str, Any] = field(default_factory=dict)
    extracted_text: str = ""
    ocr_confidence: float = 0.0
    ocr_page_count: int = 0
    entities: dict[str, Any] = field(default_factory=dict)
    policy_clauses: list[dict[str, Any]] = field(default_factory=list)
    policy_result: dict[str, Any] = field(default_factory=dict)
    fraud_result: dict[str, Any] = field(default_factory=dict)
    hospital_verification: dict[str, Any] = field(default_factory=dict)
    duplicate_result: dict[str, Any] = field(default_factory=dict)
    trust_result: dict[str, Any] = field(default_factory=dict)
    reflection_notes: list[str] = field(default_factory=list)
    intermediate_decisions: list[dict[str, Any]] = field(default_factory=list)
    recommendation: dict[str, Any] = field(default_factory=dict)
    decision: str = "Pending"
    routing_hint: str = "continue"
    confidence: float = 0.0
    confidence_score: float = 0.0
    retries: dict[str, int] = field(default_factory=lambda: {"ocr": 0, "entities": 0})
    agent_trace: list[dict[str, Any]] = field(default_factory=list)
    reasoning_summaries: dict[str, str] = field(default_factory=dict)
    source_references: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "Pending"
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    final_decision: str = ""

    @classmethod
    def from_state(cls, state: dict[str, Any] | None) -> "ClaimMemory":
        state = dict(state or {})
        state.setdefault("retries", {"ocr": 0, "entities": 0})
        state.setdefault("form_data", {})
        state.setdefault("ocr_output", {})
        state.setdefault("entities", {})
        state.setdefault("policy_clauses", [])
        state.setdefault("policy_result", {})
        state.setdefault("fraud_result", {})
        state.setdefault("hospital_verification", {})
        state.setdefault("duplicate_result", {})
        state.setdefault("trust_result", {})
        state.setdefault("reflection_notes", [])
        state.setdefault("intermediate_decisions", [])
        state.setdefault("recommendation", {})
        state.setdefault("decision", "Pending")
        state.setdefault("routing_hint", "continue")
        state.setdefault("agent_trace", [])
        state.setdefault("reasoning_summaries", {})
        state.setdefault("source_references", [])
        state.setdefault("errors", [])
        state.setdefault("warnings", [])
        state.setdefault("metadata", {})
        state.setdefault("status", "Pending")
        state.setdefault("created_at", _utcnow())
        state.setdefault("updated_at", _utcnow())
        state.setdefault("final_decision", "")
        state["amount"] = _float(state.get("amount"))
        state["ocr_confidence"] = _float(state.get("ocr_confidence"))
        state["confidence"] = _float(state.get("confidence"))
        state["confidence_score"] = _float(state.get("confidence_score"))
        state["ocr_page_count"] = int(state.get("ocr_page_count") or 0)
        return cls(**state)

    def to_state(self) -> ClaimGraphState:
        self.updated_at = _utcnow()
        return asdict(self)

    def add_reasoning(self, agent_name: str, summary: str) -> None:
        self.reasoning_summaries[agent_name] = summary

    def add_source_reference(self, reference: str) -> None:
        reference = str(reference or "").strip()
        if reference and reference not in self.source_references:
            self.source_references.append(reference)

    def add_error(self, agent_name: str, message: str, *, retryable: bool = False) -> None:
        self.errors.append(
            {
                "agent": agent_name,
                "message": message,
                "retryable": retryable,
                "timestamp": _utcnow(),
            }
        )

    def add_trace(
        self,
        *,
        agent: str,
        status: str,
        duration_ms: float,
        confidence: float = 0.0,
        retries: int = 0,
        summary: str = "",
        error: str = "",
        execution_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.agent_trace.append(
            {
                "agent": agent,
                "status": status,
                "duration_ms": round(duration_ms, 2),
                "confidence": round(_float(confidence), 3),
                "retries": retries,
                "summary": summary,
                "error": error,
                "execution_id": execution_id,
                "metadata": metadata or {},
                "timestamp": _utcnow(),
            }
        )

    def add_decision(self, decision: str, reasoning: str, *, status: str | None = None) -> None:
        self.decision = decision
        self.final_decision = decision
        self.metadata["decision_reasoning"] = reasoning
        if status:
            self.status = status
        self.intermediate_decisions.append(
            {
                "decision": decision,
                "reasoning": reasoning,
                "timestamp": _utcnow(),
            }
        )

    def missing_entity_fields(self) -> list[str]:
        critical_fields = [
            "patient_name",
            "hospital_name",
            "claim_amount",
            "invoice_number",
            "admission_date",
            "discharge_date",
            "doctor_name",
        ]
        return [field for field in critical_fields if not self.entities.get(field)]

    def summarize(self) -> str:
        parts = []
        if self.decision:
            parts.append(f"Decision: {self.decision}")
        if self.trust_result.get("score") is not None:
            parts.append(f"Trust: {self.trust_result.get('score')}")
        if self.policy_result.get("eligibility"):
            parts.append(f"Policy: {self.policy_result.get('eligibility')}")
        if self.fraud_result.get("fraud_level"):
            parts.append(f"Fraud: {self.fraud_result.get('fraud_level')}")
        return " | ".join(parts)

    def to_claim_document(self) -> dict[str, Any]:
        now = _utcnow()
        claim_doc = {
            "claim_id": self.claim_id,
            "mobile": self.mobile,
            "name": self.name,
            "hospital": self.hospital,
            "amount": float(self.amount),
            "bill_url": self.bill_url,
            "extracted_text": self.extracted_text,
            "entities": self.entities,
            "image_hash": self.fraud_result.get("image_hash"),
            "duplicate_hash": self.fraud_result.get("duplicate_hash") or self.duplicate_result.get("duplicate_hash"),
            "duplicate_result": self.duplicate_result or self.fraud_result.get("duplicate_result", {}),
            "ai_result": self.policy_result,
            "fraud_result": self.fraud_result,
            "trust_result": self.trust_result,
            "rag_result": self.policy_result,
            "officer_note": self.form_data.get("officer_note", ""),
            "citizen_remarks_submitted_at": self.form_data.get("citizen_remarks_submitted_at"),
            "status": self.status,
            "confidence_score": self.confidence_score or self.confidence,
            "ocr_confidence": self.ocr_confidence,
            "ocr_confidence_score": self.ocr_confidence,
            "trust_score": self.trust_result.get("score", 0.0),
            "trust_level": self.trust_result.get("level", "LOW"),
            "fraud_level": self.fraud_result.get("fraud_level", "LOW"),
            "processing_status": "completed",
            "date": now,
            "created_at": self.created_at,
            "updated_at": now,
            "ocr_page_count": self.ocr_page_count,
            "agent_trace": self.agent_trace,
            "reflection_notes": self.reflection_notes,
            "final_decision": self.final_decision or self.decision,
            "recommendation": self.recommendation,
            "policy_clauses": self.policy_clauses,
            "source_references": self.source_references,
            "reasoning_summaries": self.reasoning_summaries,
            "intermediate_decisions": self.intermediate_decisions,
            "workflow_metadata": self.metadata,
            "retries": self.retries,
            "errors": self.errors,
            "warnings": self.warnings,
        }
        claim_doc.update({k: v for k, v in self.form_data.items() if k not in claim_doc})
        return claim_doc
