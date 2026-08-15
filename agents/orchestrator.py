"""Main Orchestrator Agent.

Understands what the user is asking for, reads the currently selected
resource, decides which registered specialist agents are relevant, and merges
their results into a single response. No specialist agents are implemented
here - they register into this orchestrator (see register_agent) as they're
built.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agents.base import SpecialistAgent
from agents.context import apply_resource_context
from services.resource_service import ResourceService

logger = logging.getLogger(__name__)

_CAPABILITY_KEYWORDS = {
    "azure_infrastructure": ("resource", "dependen", "relationship", "metadata", "infrastructure"),
    "aks": ("cluster", "namespace", "deployment", "pod", "kubernetes", "aks", "k8s"),
    "gitlab": ("pipeline", "job", "commit", "merge request", "gitlab", "ci/cd", "build"),
    "gitlab_investigation": (
        "why did", "why is", "why does", "root cause", "what changed", "what broke",
        "investigate failure", "pipeline fail", "failed pipeline", "broke the build", "regression",
    ),
    "observability": ("metric", "alert", "health", "latency", "error rate", "availability"),
    "finops": ("cost", "spend", "budget", "utilization", "optimization"),
}


@dataclass
class Intent:
    """Structured understanding of a user message"""
    action: str  # "investigate" | "chat"
    capabilities: List[str] = field(default_factory=list)
    raw_message: str = ""


def understand_intent(message: str) -> Intent:
    """Classify a user message into an action and the capabilities it needs.

    Keyword-based for now; swap in an llm.BaseLLMClient-backed classifier later
    without changing the orchestrator's public interface.
    """
    message_lower = (message or "").lower()
    capabilities = [
        capability
        for capability, keywords in _CAPABILITY_KEYWORDS.items()
        if any(keyword in message_lower for keyword in keywords)
    ]
    action = "investigate" if capabilities else "chat"
    return Intent(action=action, capabilities=capabilities, raw_message=message or "")


class Orchestrator:
    """Routes a user request to the specialist agents needed to answer it, then merges their output"""

    def __init__(self, resource_service: Optional[ResourceService] = None):
        self.resource_service = resource_service or ResourceService()
        self._agents: Dict[str, SpecialistAgent] = {}

    def register_agent(self, capability: str, agent: SpecialistAgent) -> None:
        """Register a specialist agent under the capability name it handles (e.g. 'azure_infrastructure')"""
        self._agents[capability] = agent

    def read_selected_resource(self, resource_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Look up the currently selected resource, if any"""
        if not resource_id:
            return None
        return self.resource_service.get_resource_by_id(resource_id)

    def decide_agents(self, intent: Intent) -> Dict[str, SpecialistAgent]:
        """Pick which registered specialist agents are relevant to this intent, keyed by capability"""
        return {
            capability: self._agents[capability]
            for capability in intent.capabilities
            if capability in self._agents
        }

    def merge_results(self, results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Combine specialist agent outputs into a single results dict, keyed by capability"""
        return dict(results)

    def understand(self, message: str, resource_id: Optional[str] = None) -> Intent:
        """Detect intent and ground it in the selected resource, if any (see agents.context)."""
        intent = understand_intent(message)
        return apply_resource_context(intent, resource_id, self._agents.keys())

    def gather_evidence(self, intent: Intent, resource_id: Optional[str]) -> Dict[str, Any]:
        """Run only the specialist agents this intent requires and merge their output.

        Requires a selected resource - without one there is nothing to fetch evidence for,
        regardless of which capabilities were detected.
        """
        agents = self.decide_agents(intent) if resource_id else {}
        results: Dict[str, Dict[str, Any]] = {}
        for capability, agent in agents.items():
            try:
                results[capability] = agent.run(resource_id)
            except Exception as exc:
                logger.error("Specialist agent '%s' failed for resource %s: %s", capability, resource_id, exc)
                results[capability] = {"agent": capability, "found": False, "error": str(exc)}
        return self.merge_results(results)

    def handle(self, message: str, resource_id: Optional[str] = None) -> Dict[str, Any]:
        """Full orchestration pass: intent -> resource -> specialist agents -> merged result"""
        intent = self.understand(message, resource_id)
        resource = self.read_selected_resource(resource_id)
        results = self.gather_evidence(intent, resource_id)

        return {
            "intent": intent,
            "resource": resource,
            "results": results,
        }
