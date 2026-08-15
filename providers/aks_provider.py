from typing import Any, Dict, List, Optional
from .base_provider import BaseProvider
from .azure.aks import AzureAKS


class MockAKSProvider(BaseProvider):
    """AKS provider for Kubernetes cluster management, backed by live Azure/Kubernetes data.

    Cluster discovery comes from Azure Resource Manager; namespace/node/workload/event/log
    data comes from the cluster's own Kubernetes API and is always scoped to a `cluster_id`
    (Kubernetes objects aren't ARM resources, so they can't be looked up by a bare ID alone
    the way Azure resources can). All operations are read-only.
    """

    def __init__(self, aks: Optional[AzureAKS] = None):
        self._aks = aks or AzureAKS()
        self._clusters_cache: Optional[List[Dict[str, Any]]] = None

    def get_clusters(self) -> List[Dict[str, Any]]:
        """Get all AKS clusters in the subscription.

        Cached on this instance after the first call - cluster discovery is a live ARM call
        (ContainerServiceClient.managed_clusters.list()) and this method used to be called on
        nearly every Streamlit rerun (the sidebar and the AKS Workspace page both call it
        unconditionally), so without caching almost any interaction anywhere in the app re-issued
        it. Matches the caching MockAzureProvider.get_all_resources() already does for the
        equivalent Resource Graph discovery call.
        """
        if self._clusters_cache is None:
            self._clusters_cache = self._aks.get_clusters()
        return self._clusters_cache

    def get_cluster(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific cluster by ARM resource ID or short name"""
        return self._aks.get_cluster(cluster_id)

    def get_namespaces(self, cluster_id: str) -> List[Dict[str, Any]]:
        """Get all namespaces for a specific cluster"""
        return self._aks.get_namespaces(cluster_id)

    def get_namespace(self, cluster_id: str, namespace_name: str) -> Optional[Dict[str, Any]]:
        """Get a specific namespace by name"""
        return next((ns for ns in self._aks.get_namespaces(cluster_id) if ns.get("name") == namespace_name), None)

    def get_nodes(self, cluster_id: str) -> List[Dict[str, Any]]:
        """Get all nodes for a specific cluster"""
        return self._aks.get_nodes(cluster_id)

    def get_deployments(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all deployments for a specific cluster (optionally filtered to one namespace)"""
        return self._aks.get_deployments(cluster_id, namespace)

    def get_namespace_deployments(self, cluster_id: str, namespace: str) -> List[Dict[str, Any]]:
        """Get deployments for a specific namespace"""
        return self._aks.get_deployments(cluster_id, namespace)

    def get_deployment(self, cluster_id: str, namespace: str, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific deployment by cluster/namespace/name"""
        return next(
            (d for d in self._aks.get_deployments(cluster_id, namespace) if d.get("name") == name), None
        )

    def get_replicasets(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all ReplicaSets for a specific cluster (optionally filtered to one namespace)"""
        return self._aks.get_replicasets(cluster_id, namespace)

    def get_pods(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all pods for a specific cluster (optionally filtered to one namespace)"""
        return self._aks.get_pods(cluster_id, namespace)

    def get_pod(self, cluster_id: str, namespace: str, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific pod by cluster/namespace/name"""
        return next((p for p in self._aks.get_pods(cluster_id, namespace) if p.get("name") == name), None)

    def get_deployment_pods(self, cluster_id: str, namespace: str, deployment_name: str) -> List[Dict[str, Any]]:
        """Get pods owned by a specific deployment (via its ReplicaSet)"""
        owning_replicasets = {
            rs.get("name") for rs in self._aks.get_replicasets(cluster_id, namespace)
            if rs.get("owner") == deployment_name
        }
        return [p for p in self._aks.get_pods(cluster_id, namespace) if p.get("owner") in owning_replicasets]

    def get_services(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all services for a specific cluster (optionally filtered to one namespace)"""
        return self._aks.get_services(cluster_id, namespace)

    def get_service(self, cluster_id: str, namespace: str, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific service by cluster/namespace/name"""
        return next((s for s in self._aks.get_services(cluster_id, namespace) if s.get("name") == name), None)

    def get_ingress(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all ingress resources for a specific cluster (optionally filtered to one namespace)"""
        return self._aks.get_ingresses(cluster_id, namespace)

    def get_ingress_resource(self, cluster_id: str, namespace: str, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific ingress resource by cluster/namespace/name"""
        return next((i for i in self._aks.get_ingresses(cluster_id, namespace) if i.get("name") == name), None)

    def get_events(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get recent events for a cluster (optionally filtered to one namespace)"""
        return self._aks.get_events(cluster_id, namespace=namespace)

    def get_pod_logs(
        self, cluster_id: str, namespace: str, pod_name: str, container: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get the latest logs for a specific pod"""
        return self._aks.get_pod_logs(cluster_id, namespace, pod_name, container=container)

    def get_pod_events(self, cluster_id: str, namespace: str, pod_name: str) -> List[Dict[str, Any]]:
        """Get events for a specific pod"""
        return self._aks.get_pod_events(cluster_id, namespace, pod_name)

    def get_configmaps(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get ConfigMaps for a cluster (optionally filtered to one namespace) - names and data keys only"""
        return self._aks.get_configmaps(cluster_id, namespace)

    def get_secrets(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get Secrets for a cluster (optionally filtered to one namespace) - names and type only, never values"""
        return self._aks.get_secrets(cluster_id, namespace)
