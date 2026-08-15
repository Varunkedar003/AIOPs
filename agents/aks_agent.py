"""AKS Agent.

Specialist agent that analyzes AKS clusters, namespaces, deployments, pods,
services, events, and logs. Built entirely on MockAKSProvider - no direct
JSON access happens here.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from agents.base import SpecialistAgent
from providers.aks_provider import MockAKSProvider
from utils.resource_id import normalize_resource_id


@dataclass
class AKSClusterReport:
    """Structured output of an AKS Agent cluster investigation"""
    agent: str = "aks"
    cluster_id: str = ""
    found: bool = False
    cluster: Dict[str, Any] = field(default_factory=dict)
    namespaces: List[Dict[str, Any]] = field(default_factory=list)
    deployments: List[Dict[str, Any]] = field(default_factory=list)
    pods: List[Dict[str, Any]] = field(default_factory=list)
    services: List[Dict[str, Any]] = field(default_factory=list)


class AKSAgent(SpecialistAgent):
    """Specialist agent covering AKS clusters, namespaces, deployments, pods, services, events, and logs"""

    name = "aks"

    def __init__(self, aks_provider: Optional[MockAKSProvider] = None):
        self.aks_provider = aks_provider or MockAKSProvider()

    def _find_cluster(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """Resolve a cluster by full resource ID or short name"""
        cluster = self.aks_provider.get_cluster(cluster_id)
        if cluster:
            return cluster

        normalized = normalize_resource_id(cluster_id)
        if normalized and normalized != cluster_id:
            cluster = self.aks_provider.get_cluster(normalized)
            if cluster:
                return cluster

        for candidate in self.aks_provider.get_clusters():
            if candidate.get("name") in (cluster_id, normalized):
                return candidate

        return None

    def analyze_clusters(self, cluster_id: str) -> Dict[str, Any]:
        """Analyze a single cluster's metadata"""
        return self._find_cluster(cluster_id) or {}

    def analyze_namespaces(self, cluster_id: str) -> List[Dict[str, Any]]:
        """Analyze namespaces for a cluster"""
        return self.aks_provider.get_namespaces(cluster_id)

    def analyze_deployments(self, cluster_id: str) -> List[Dict[str, Any]]:
        """Analyze all deployments for a cluster"""
        return self.aks_provider.get_deployments(cluster_id)

    def analyze_pods(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Analyze pods in a cluster, optionally scoped to one namespace"""
        return self.aks_provider.get_pods(cluster_id, namespace)

    def analyze_services(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Analyze services in a cluster, optionally scoped to one namespace"""
        return self.aks_provider.get_services(cluster_id, namespace)

    def analyze_events(self, cluster_id: str, namespace: str, pod_name: str) -> List[Dict[str, Any]]:
        """Analyze events for a specific pod"""
        return self.aks_provider.get_pod_events(cluster_id, namespace, pod_name)

    def analyze_logs(self, cluster_id: str, namespace: str, pod_name: str) -> Dict[str, Any]:
        """Analyze logs for a specific pod"""
        return self.aks_provider.get_pod_logs(cluster_id, namespace, pod_name)

    def investigate_cluster(self, cluster_id: str) -> AKSClusterReport:
        """Build a structured cluster-wide report: cluster, namespaces, deployments, pods, services"""
        cluster = self.analyze_clusters(cluster_id)
        if not cluster:
            return AKSClusterReport(cluster_id=cluster_id, found=False)

        resolved_id = cluster.get("id", cluster_id)
        namespaces = self.analyze_namespaces(resolved_id)
        deployments = self.analyze_deployments(resolved_id)
        pods = self.analyze_pods(resolved_id)
        services = self.analyze_services(resolved_id)

        return AKSClusterReport(
            cluster_id=cluster_id,
            found=True,
            cluster=cluster,
            namespaces=namespaces,
            deployments=deployments,
            pods=pods,
            services=services,
        )

    def run(self, resource_id: str) -> Dict[str, Any]:
        """SpecialistAgent interface: cluster-wide report as a plain dict for the orchestrator to merge.

        For pod-level events/logs, use analyze_events()/analyze_logs() directly with cluster/namespace/pod.
        """
        return asdict(self.investigate_cluster(resource_id))
