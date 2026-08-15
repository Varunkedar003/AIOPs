"""FinOps Agent.

Specialist agent that analyzes cost, resource utilization, optimization
opportunities, and cost summary for a resource. Built entirely on
MockCostProvider and MockObservabilityProvider - no direct JSON access
happens here.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from agents.base import SpecialistAgent
from providers.cost_provider import MockCostProvider
from providers.observability_provider import MockObservabilityProvider

_UTILIZATION_METRIC_TYPES = ("cpu", "memory")


@dataclass
class FinOpsReport:
    """Structured output of a FinOps Agent investigation"""
    agent: str = "finops"
    resource_id: str = ""
    found: bool = False
    cost: Dict[str, Any] = field(default_factory=dict)
    utilization: Dict[str, Any] = field(default_factory=dict)
    optimization: Dict[str, Any] = field(default_factory=dict)
    cost_summary: Dict[str, Any] = field(default_factory=dict)


class FinOpsAgent(SpecialistAgent):
    """Specialist agent covering cost, resource utilization, optimization opportunities, and cost summary"""

    name = "finops"

    def __init__(
        self,
        cost_provider: Optional[MockCostProvider] = None,
        observability_provider: Optional[MockObservabilityProvider] = None,
    ):
        self.cost_provider = cost_provider or MockCostProvider()
        self.observability_provider = observability_provider or MockObservabilityProvider()

    def analyze_cost(self, resource_id: str) -> Dict[str, Any]:
        """Analyze current monthly/daily cost for a resource"""
        return self.cost_provider.get_monthly_cost(resource_id) or {}

    def analyze_utilization(self, resource_id: str) -> Dict[str, Any]:
        """Analyze resource utilization (CPU/memory) for a resource"""
        utilization = {}
        for metric_type in _UTILIZATION_METRIC_TYPES:
            metrics = self.observability_provider.get_metrics_by_type(resource_id, metric_type)
            if metrics:
                utilization[metric_type] = metrics[0]
        return utilization

    def analyze_optimization(self, resource_id: str) -> Dict[str, Any]:
        """Analyze cost optimization opportunities and trend for a resource"""
        all_opportunities = self.cost_provider.get_cost_optimization_opportunities()
        return {
            "trend": self.cost_provider.get_cost_trends(resource_id),
            "opportunities": [o for o in all_opportunities if o.get("resource_id") == resource_id],
        }

    def analyze_cost_summary(self, resource_id: str) -> Dict[str, Any]:
        """Analyze a detailed cost breakdown/summary for a resource"""
        return self.cost_provider.get_cost_breakdown(resource_id) or {}

    def investigate(self, resource_id: str) -> FinOpsReport:
        """Build a structured FinOps report covering cost, utilization, optimization, and cost summary"""
        cost = self.analyze_cost(resource_id)
        if not cost:
            return FinOpsReport(resource_id=resource_id, found=False)

        return FinOpsReport(
            resource_id=resource_id,
            found=True,
            cost=cost,
            utilization=self.analyze_utilization(resource_id),
            optimization=self.analyze_optimization(resource_id),
            cost_summary=self.analyze_cost_summary(resource_id),
        )

    def run(self, resource_id: str) -> Dict[str, Any]:
        """SpecialistAgent interface: same investigation, as a plain dict for the orchestrator to merge"""
        return asdict(self.investigate(resource_id))
