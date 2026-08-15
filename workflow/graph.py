"""Chat workflow graph (Task 18): every user message flows through the same
Intent -> Agents -> Claude pipeline - detect intent, run only the specialist and
CrewAI agents that intent requires, then hand their reports and all collected
evidence to Claude Sonnet for the final, evidence-grounded reply. There is no
separate templated "chat" path: a message with no matched capability still goes
through Claude (see synthesis/claude_synthesizer.py for how it handles that case).
"""
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from agents.aks_agent import AKSAgent
from agents.azure_infrastructure_agent import AzureInfrastructureAgent
from agents.crew import InvestigationCrew
from agents.finops_agent import FinOpsAgent
from agents.gitlab_agent import GitLabAgent, GitLabInvestigationAgent
from agents.observability_agent import ObservabilityAgent
from agents.orchestrator import Orchestrator
from synthesis import ClaudeSynthesizer
from workflow.router import NODE_AGENTS, NODE_CLAUDE, NODE_COMPLETE, NODE_INTENT
from workflow.state import AgentState

_MAX_HISTORY_TURNS = 6  # prior turns of context sent to Claude, for follow-up questions


def _default_orchestrator() -> Orchestrator:
    """Assemble the Main Orchestrator Agent with every existing specialist agent registered"""
    orchestrator = Orchestrator()
    orchestrator.register_agent("azure_infrastructure", AzureInfrastructureAgent())
    orchestrator.register_agent("aks", AKSAgent())
    orchestrator.register_agent("gitlab", GitLabAgent())
    orchestrator.register_agent("gitlab_investigation", GitLabInvestigationAgent())
    orchestrator.register_agent("observability", ObservabilityAgent())
    orchestrator.register_agent("finops", FinOpsAgent())
    return orchestrator


def _last_user_text(messages: List[Any]) -> str:
    """Get the most recent human message's text"""
    for message in reversed(messages):
        if getattr(message, "type", None) == "human":
            return message.content
    return ""


def _recent_history(messages: List[Any]) -> List[Dict[str, str]]:
    """Prior turns (excluding the current, still-unanswered message) as plain role/content
    dicts, so Claude has continuity on follow-up questions."""
    prior = messages[:-1] if messages else []
    history = []
    for message in prior[-_MAX_HISTORY_TURNS:]:
        role = "user" if getattr(message, "type", None) == "human" else "assistant"
        content = message.content if isinstance(message.content, str) else str(message.content)
        history.append({"role": role, "content": content})
    return history


def _resource_display_name(orchestrator: Orchestrator, resource_id: Optional[str]) -> Optional[str]:
    if not resource_id:
        return None
    resource = orchestrator.read_selected_resource(resource_id)
    return (resource or {}).get("name") if resource else None


def _make_intent_node(orchestrator: Orchestrator):
    """Stage 1 (Intent): classify the message and ground it in the selected resource."""

    def _node(state: AgentState) -> Dict[str, Any]:
        message_text = _last_user_text(state["messages"])
        resource_id = state.get("selected_resource_id")

        intent = orchestrator.understand(message_text, resource_id)

        investigation = dict(state.get("investigation") or {})
        investigation["stage"] = "intent"
        investigation["capabilities"] = list(intent.capabilities)
        investigation["resource_name"] = _resource_display_name(orchestrator, resource_id)
        investigation["error"] = None
        investigation["timeline"] = (investigation.get("timeline") or []) + [
            f"Intent detected: {', '.join(intent.capabilities) if intent.capabilities else 'general question'}"
        ]
        return {"investigation": investigation}

    return _node


def _make_agents_node(orchestrator: Orchestrator, crew: InvestigationCrew):
    """Stage 2 (Agents): gather evidence via only the required specialist agents, then run
    only the corresponding CrewAI domain agents on that evidence."""

    def _node(state: AgentState) -> Dict[str, Any]:
        message_text = _last_user_text(state["messages"])
        resource_id = state.get("selected_resource_id")

        investigation = dict(state.get("investigation") or {})
        investigation["stage"] = "agents"

        intent = orchestrator.understand(message_text, resource_id)
        results = orchestrator.gather_evidence(intent, resource_id)

        domain_reports = crew.investigate(resource_id, results, question=message_text) if results else {}
        domain_reports_dicts = {domain: report.model_dump() for domain, report in domain_reports.items()}

        evidence_sources = sorted(
            capability for capability, result in results.items() if result.get("found", True)
        )

        investigation["results"] = results
        investigation["domain_reports"] = domain_reports_dicts
        investigation["evidence_sources"] = evidence_sources
        timeline_entry = f"Agents: ran {', '.join(results.keys())}" if results else "Agents: none required"
        if domain_reports_dicts:
            timeline_entry += f"; analyzed {', '.join(domain_reports_dicts.keys())}"
        investigation["timeline"] = (investigation.get("timeline") or []) + [timeline_entry]
        return {"investigation": investigation}

    return _node


def _make_claude_announce_node():
    """Stage 3 (Claude): a trivial, instant node whose only job is to make "Claude is now
    running" an observable point in `.stream()`, before the (slower) API call happens in
    NODE_COMPLETE. Splitting these two gives the progress UI a real "Claude" frame instead of
    jumping straight from "agents done" to "everything done"."""

    def _node(state: AgentState) -> Dict[str, Any]:
        investigation = dict(state.get("investigation") or {})
        investigation["stage"] = "claude"
        return {"investigation": investigation}

    return _node


def _make_complete_node(synthesizer: ClaudeSynthesizer):
    """Stage 4 (Complete): call Claude Sonnet to correlate domain reports + evidence into the
    final reply, and finish the turn."""

    def _node(state: AgentState) -> Dict[str, Any]:
        message_text = _last_user_text(state["messages"])
        resource_id = state.get("selected_resource_id")
        history = _recent_history(state["messages"])

        investigation = dict(state.get("investigation") or {})

        outcome = synthesizer.synthesize(
            domain_reports=investigation.get("domain_reports") or {},
            evidence=investigation.get("results") or {},
            query=message_text,
            resource_id=resource_id,
            history=history,
        )

        investigation["final_report"] = outcome["structured"]
        investigation["stage"] = "complete"
        investigation["timeline"] = (investigation.get("timeline") or []) + ["Claude: synthesis complete"]
        if synthesizer.last_error:
            investigation["error"] = synthesizer.last_error

        return {
            "messages": [AIMessage(content=outcome["markdown"])],
            "investigation": investigation,
        }

    return _node


def build_graph(orchestrator: Optional[Orchestrator] = None, crew: Optional[InvestigationCrew] = None,
                 synthesizer: Optional[ClaudeSynthesizer] = None):
    """Build and compile the chat workflow graph: Intent -> Agents -> Claude -> Complete -> END.

    Every message takes the same path - use `.stream(state, stream_mode="values")` (see
    dashboard/chat.py) to observe each stage as it completes for the progress UI.
    """
    orchestrator = orchestrator or _default_orchestrator()
    crew = crew or InvestigationCrew()
    synthesizer = synthesizer or ClaudeSynthesizer()

    graph = StateGraph(AgentState)
    graph.add_node(NODE_INTENT, _make_intent_node(orchestrator))
    graph.add_node(NODE_AGENTS, _make_agents_node(orchestrator, crew))
    graph.add_node(NODE_CLAUDE, _make_claude_announce_node())
    graph.add_node(NODE_COMPLETE, _make_complete_node(synthesizer))

    graph.add_edge(START, NODE_INTENT)
    graph.add_edge(NODE_INTENT, NODE_AGENTS)
    graph.add_edge(NODE_AGENTS, NODE_CLAUDE)
    graph.add_edge(NODE_CLAUDE, NODE_COMPLETE)
    graph.add_edge(NODE_COMPLETE, END)

    return graph.compile()
