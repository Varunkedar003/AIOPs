"""Azure Infrastructure Agent.

Specialist agent that reports resource metadata, dependencies, relationships,
and a health summary for a single Azure resource. Built entirely on the
existing ResourceService/provider layer - no direct JSON access happens here.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from agents.base import SpecialistAgent
from services.resource_service import ResourceService


@dataclass
class AzureInfrastructureReport:
    """Structured output of an Azure Infrastructure Agent investigation"""
    agent: str = "azure_infrastructure"
    resource_id: str = ""
    found: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    health_summary: Dict[str, Any] = field(default_factory=dict)


class AzureInfrastructureAgent(SpecialistAgent):
    """Specialist agent covering Azure resource metadata, dependencies, relationships, and health"""

    name = "azure_infrastructure"

    def __init__(self, resource_service: Optional[ResourceService] = None):
        self.resource_service = resource_service or ResourceService()

    def investigate(self, resource_id: str) -> AzureInfrastructureReport:
        """Build a structured infrastructure report for a resource, via ResourceService only"""
        metadata = self.resource_service.get_resource_by_id(resource_id)
        if not metadata:
            return AzureInfrastructureReport(resource_id=resource_id, found=False)

        relationships = self.resource_service.get_connected_resources(resource_id)
        health_summary = self.resource_service.get_resource_metrics_summary(resource_id)

        return AzureInfrastructureReport(
            resource_id=resource_id,
            found=True,
            metadata=metadata,
            dependencies=list(metadata.get("dependencies", [])),
            relationships=relationships,
            health_summary=health_summary,
        )

    def run(self, resource_id: str) -> Dict[str, Any]:
        """SpecialistAgent interface: same investigation, as a plain dict for the orchestrator to merge"""
        return asdict(self.investigate(resource_id))
