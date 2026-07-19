from __future__ import annotations

from functools import lru_cache
from typing import Callable, Literal

from langgraph.graph import END, START, StateGraph

from agents.decision_agent import DecisionAgent
from agents.duplicate_agent import DuplicateAgent
from agents.entity_agent import EntityAgent
from agents.fraud_agent import FraudAgent
from agents.hospital_agent import HospitalAgent
from agents.ocr_agent import OCRAgent
from agents.policy_agent import PolicyAgent
from agents.recommendation_agent import RecommendationAgent
from agents.reflection_agent import ReflectionAgent
from agents.trust_agent import TrustAgent
from memory.claim_memory import ClaimGraphState


def _node(agent) -> Callable[[ClaimGraphState], ClaimGraphState]:
    return lambda state: agent.execute(state)


def _route_after_ocr(state: ClaimGraphState) -> Literal["ocr", "entity"]:
    if state.get("routing_hint") == "retry_ocr" and int(state.get("retries", {}).get("ocr", 0) or 0) < 1:
        return "ocr"
    return "entity"


def _route_after_entity(state: ClaimGraphState) -> Literal["entity", "policy"]:
    retries = state.get("retries", {}) or {}
    if state.get("routing_hint") == "retry_entities" and int(retries.get("entity", retries.get("entities", 0)) or 0) < 1:
        return "entity"
    return "policy"


def _route_after_reflection(state: ClaimGraphState) -> Literal["ocr", "entity", "decision"]:
    hint = state.get("routing_hint")
    retries = state.get("retries", {}) or {}
    if hint == "retry_ocr" and int(retries.get("ocr", 0) or 0) < 1:
        return "ocr"
    if hint == "retry_entities" and int(retries.get("entity", retries.get("entities", 0)) or 0) < 1:
        return "entity"
    return "decision"


@lru_cache(maxsize=1)
def build_claim_graph():
    workflow = StateGraph(ClaimGraphState)
    workflow.add_node("ocr", _node(OCRAgent()))
    workflow.add_node("entity", _node(EntityAgent()))
    workflow.add_node("policy", _node(PolicyAgent()))
    workflow.add_node("fraud", _node(FraudAgent()))
    workflow.add_node("hospital", _node(HospitalAgent()))
    workflow.add_node("duplicate", _node(DuplicateAgent()))
    workflow.add_node("trust", _node(TrustAgent()))
    workflow.add_node("reflection", _node(ReflectionAgent()))
    workflow.add_node("decision", _node(DecisionAgent()))
    workflow.add_node("recommendation", _node(RecommendationAgent()))

    workflow.add_edge(START, "ocr")
    workflow.add_conditional_edges("ocr", _route_after_ocr, {"ocr": "ocr", "entity": "entity"})
    workflow.add_conditional_edges("entity", _route_after_entity, {"entity": "entity", "policy": "policy"})
    workflow.add_edge("policy", "fraud")
    workflow.add_edge("fraud", "hospital")
    workflow.add_edge("hospital", "duplicate")
    workflow.add_edge("duplicate", "trust")
    workflow.add_edge("trust", "reflection")
    workflow.add_conditional_edges(
        "reflection",
        _route_after_reflection,
        {"ocr": "ocr", "entity": "entity", "decision": "decision"},
    )
    workflow.add_edge("decision", "recommendation")
    workflow.add_edge("recommendation", END)
    return workflow.compile()


def run_claim_graph(state: ClaimGraphState) -> ClaimGraphState:
    graph = build_claim_graph()
    result = graph.invoke(state)
    return result

