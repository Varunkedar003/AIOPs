from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph.message import add_messages


class InvestigationState(TypedDict):
    """Snapshot of the most recent Intent -> Agents -> Claude pipeline pass for the chat UI."""
    stage: str  # "idle" | "intent" | "agents" | "claude" | "complete" | "error"
    capabilities: List[str]  # domains the last message's intent matched
    resource_name: Optional[str]  # display name of the selected resource, if any
    results: Dict[str, Any]  # raw evidence from the specialist agents (agents/orchestrator), by capability
    domain_reports: Dict[str, Any]  # CrewAI domain analysis (agents/crew), by domain
    evidence_sources: List[str]  # capabilities that actually returned evidence this turn
    final_report: Optional[Dict[str, Any]]  # Claude's structured FinalInvestigationReport (synthesis/)
    timeline: List[str]  # human-readable log of what happened this turn, oldest first
    error: Optional[str]


class AgentState(TypedDict):
    """Shared state carried through the workflow graph"""
    messages: Annotated[List, add_messages]
    selected_resource_id: Optional[str]
    investigation: InvestigationState


def new_investigation_state() -> InvestigationState:
    """Default, empty investigation state"""
    return {
        "stage": "idle",
        "capabilities": [],
        "resource_name": None,
        "results": {},
        "domain_reports": {},
        "evidence_sources": [],
        "final_report": None,
        "timeline": [],
        "error": None,
    }


def initial_state(selected_resource_id: Optional[str] = None) -> AgentState:
    """Build a fresh AgentState for a new session"""
    return {
        "messages": [],
        "selected_resource_id": selected_resource_id,
        "investigation": new_investigation_state(),
    }
