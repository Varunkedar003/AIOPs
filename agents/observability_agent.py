"""Observability Agent.

Specialist agent that analyzes metrics, alerts, health, latency, error rate,
and availability for a resource. Built entirely on MockObservabilityProvider -
no direct JSON access happens here.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from agents.base import SpecialistAgent
from providers.observability_provider import MockObservabilityProvider


@dataclass
class ObservabilityReport:
    """Structured output of an Observability Agent investigation"""
    agent: str = "observability"
    resource_id: str = ""
    found: bool = False
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    health: Dict[str, Any] = field(default_factory=dict)
    latency: List[Dict[str, Any]] = field(default_factory=list)
    error_rate: List[Dict[str, Any]] = field(default_factory=list)
    availability: List[Dict[str, Any]] = field(default_factory=list)


class ObservabilityAgent(SpecialistAgent):
    """Specialist agent covering metrics, alerts, health, latency, error rate, and availability"""

    name = "observability"

    def __init__(self, observability_provider: Optional[MockObservabilityProvider] = None):
        self.observability_provider = observability_provider or MockObservabilityProvider()

    def analyze_metrics(self, resource_id: str) -> List[Dict[str, Any]]:
        """Analyze all metrics for a resource"""
        return self.observability_provider.get_metrics(resource_id)

    def analyze_alerts(self, resource_id: str) -> List[Dict[str, Any]]:
        """Analyze alerts for a resource"""
        return self.observability_provider.get_alerts(resource_id)

    def analyze_health(self, resource_id: str) -> Dict[str, Any]:
        """Analyze overall health status for a resource"""
        return self.observability_provider.get_health(resource_id)

    def analyze_latency(self, resource_id: str) -> List[Dict[str, Any]]:
        """Analyze latency metrics for a resource"""
        return self.observability_provider.get_metrics_by_type(resource_id, "latency")

    def analyze_error_rate(self, resource_id: str) -> List[Dict[str, Any]]:
        """Analyze error rate metrics for a resource"""
        return self.observability_provider.get_metrics_by_type(resource_id, "error_rate")

    def analyze_availability(self, resource_id: str) -> List[Dict[str, Any]]:
        """Analyze availability metrics for a resource"""
        return self.observability_provider.get_metrics_by_type(resource_id, "availability")

    def investigate(self, resource_id: str) -> ObservabilityReport:
        """Build a structured observability report covering metrics, alerts, health, latency, error rate, and availability"""
        metrics = self.analyze_metrics(resource_id)
        alerts = self.analyze_alerts(resource_id)
        health = self.analyze_health(resource_id)

        found = bool(metrics) or bool(alerts) or health.get("resource_type", "unknown") != "unknown"
        if not found:
            return ObservabilityReport(resource_id=resource_id, found=False, health=health)

        return ObservabilityReport(
            resource_id=resource_id,
            found=True,
            metrics=metrics,
            alerts=alerts,
            health=health,
            latency=self.analyze_latency(resource_id),
            error_rate=self.analyze_error_rate(resource_id),
            availability=self.analyze_availability(resource_id),
        )

    def run(self, resource_id: str) -> Dict[str, Any]:
        """SpecialistAgent interface: same investigation, as a plain dict for the orchestrator to merge"""
        return asdict(self.investigate(resource_id))
